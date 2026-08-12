"""Orchestrace: co se má udělat (`detect`) a udělání jednoho měsíce (`run_month`).

Stav není nikde zvlášť -- stav *je* to, co je zapsané. `detect` porovná ETag ze zdroje
proti sidecaru u uloženého raw souboru; když sidecar chybí, měsíc se prostě zpracuje
znovu (zápis je idempotentní).
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import UTC, date, datetime, timedelta

import polars as pl

from . import dq, source, storage
from .config import Config
from .errors import DataQualityError, PermanentError
from .log import bind, log
from .transform import transform

VERSION = "0.1.0"


def new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def layout(cfg: Config) -> storage.Layout:
    return storage.Layout(cfg.raw_uri, cfg.curated_uri, cfg.rejects_uri)


def window(cfg: Config, today: date | None = None) -> list[tuple[int, int]]:
    """Okno končí u předchozího měsíce -- aktuální měsíc zdroj publikovat nemůže.
    Šest měsíců pokryje naměřený lag (26-85 dní) i dávkový restatement."""
    cursor = (today or date.today()).replace(day=1) - timedelta(days=1)
    months = []
    for _ in range(cfg.lookback_months):
        months.append((cursor.year, cursor.month))
        cursor = cursor.replace(day=1) - timedelta(days=1)
    return months


def month_range(from_month: str | None, to_month: str | None) -> list[tuple[int, int]] | None:
    """Backfill je explicitní rozsah, ne druhý DAG a ne druhá cesta kódem."""
    if not from_month and not to_month:
        return None
    start = _parse_month(from_month or to_month)
    end = _parse_month(to_month or from_month)
    months, cursor = [], start
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=7)).replace(day=1)
    return months


def _parse_month(value: str) -> date:
    year, month = value.split("-")
    return date(int(year), int(month), 1)


def detect(cfg: Config, months: list[tuple[int, int]] | None = None, force: bool = False) -> dict:
    paths = layout(cfg)
    candidates = months or window(cfg)
    work, published = [], []

    for year, month in candidates:
        meta = source.head(cfg.month_url(year, month))
        if meta is None:
            log("month_not_published", year=year, month=month)
            continue
        published.append((year, month, meta))

        stored = _stored_meta(paths, year, month)
        has_output = storage.exists(paths.curated_file(year, month))
        if force:
            reason = "force"
        elif stored is None:
            reason = "new"
        elif stored.get("etag") != meta.etag:
            reason = "etag_changed"
        elif not has_output:
            reason = "missing_output"
        else:
            log("month_unchanged", year=year, month=month, etag=meta.etag)
            continue
        work.append({"year": year, "month": month, "etag": meta.etag, "reason": reason})

    newest = max(published, key=lambda item: (item[0], item[1])) if published else None
    return {
        "months": work,
        "source_newest": f"{newest[0]:04d}-{newest[1]:02d}" if newest else None,
        "source_age_days": _age_days(newest[0], newest[1]) if newest else None,
        "ingest_gap": [
            f"{year:04d}-{month:02d}"
            for year, month, _ in published
            if not storage.exists(paths.curated_file(year, month))
        ],
    }


def check_freshness(cfg: Config) -> dict:
    """Zelený DAG nesmí lhát. Dvě různá selhání, dvě různá tvrzení: publikovaný měsíc bez
    výstupu (naše chyba, bez časového prahu) a zdroj, který přestal publikovat."""
    state = detect(cfg)
    if state["ingest_gap"]:
        raise DataQualityError(f"publikované měsíce bez výstupu: {state['ingest_gap']}")
    if state["source_newest"] is None:
        raise DataQualityError("v okně není žádný publikovaný měsíc -- změnilo se URL schéma?")
    if state["source_age_days"] > cfg.source_stale_days:
        raise DataQualityError(
            f"nejnovější data jsou {state['source_newest']} ({state['source_age_days']} dní);"
            f" práh je {cfg.source_stale_days}"
        )
    log("freshness_ok", **state)
    return state


def run_month(
    cfg: Config,
    year: int,
    month: int,
    run_id: str | None = None,
    trigger: str = "cli",
    expected_etag: str | None = None,
) -> dict:
    run_id = run_id or new_run_id()
    bind(run_id=run_id, year=year, month=month)
    started = time.monotonic()
    paths = layout(cfg)

    meta, sha256 = _fetch(cfg, paths, year, month, expected_etag)
    zones = _zones(cfg, paths)

    trips = pl.scan_parquet(paths.raw_file(year, month))
    schema = dq.check_contract(dict(trips.collect_schema()))
    log("contract_ok", **schema)

    rules = dq.rules(cfg, year, month)
    result = transform(trips, zones, rules)

    previous = _previous_input_rows(paths, year, month)
    result.metrics["rows"]["prev_version_input"] = _latest_input_rows(paths, year, month)
    dq.gate(result.metrics, cfg, previous)

    storage.write_parquet(paths.curated_file(year, month), result.aggregate)
    storage.write_parquet(paths.rejects_file(year, month), result.rejects)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "trigger": trigger,
        "app_version": VERSION,
        "git_sha": _git_sha(),
        "dataset": "yellow",
        "year": year,
        "month": month,
        "source": {
            "url": meta.url,
            "etag": meta.etag,
            "last_modified": meta.last_modified,
            "bytes": meta.bytes,
            "sha256": sha256,
        },
        "schema": schema,
        **result.metrics,
        "thresholds_applied": cfg.thresholds(),
        "timing": {"seconds": round(time.monotonic() - started, 1)},
    }
    storage.write_json(paths.run_file(year, month, run_id), manifest)
    log("run_ok", **manifest["rows"], seconds=manifest["timing"]["seconds"])
    return manifest


def _fetch(cfg, paths, year, month, expected_etag) -> tuple[source.SourceMeta, str | None]:
    """Raw se přepisuje, neverzuje -- lineage funguje odkazem (ETag + sha256 v manifestu),
    ne archivem. Retry transformace nestahuje 56 MB znovu."""
    url = cfg.month_url(year, month)
    remote = source.head(url)
    if remote is None:
        raise PermanentError(f"{year:04d}-{month:02d} není publikovaný")
    if expected_etag and expected_etag != remote.etag:
        log("etag_moved", expected=expected_etag, actual=remote.etag)

    stored = _stored_meta(paths, year, month)
    if stored and stored.get("etag") == remote.etag and storage.exists(paths.raw_file(year, month)):
        log("raw_reused", etag=remote.etag)
        return remote, stored.get("sha256")

    payload, meta, sha256 = source.download(url)
    storage.write_bytes(paths.raw_file(year, month), payload)
    storage.write_json(
        paths.raw_meta(year, month),
        {
            "url": meta.url,
            "etag": meta.etag,
            "last_modified": meta.last_modified,
            "bytes": meta.bytes,
            "sha256": sha256,
            "downloaded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    )
    log("raw_downloaded", etag=meta.etag, bytes=meta.bytes)
    return meta, sha256


def _zones(cfg: Config, paths: storage.Layout) -> pl.LazyFrame:
    cached = storage.join(paths.raw_uri, "taxi_zone_lookup.csv")
    if not storage.exists(cached):
        payload, _, _ = source.download(cfg.zones_url)
        storage.write_bytes(cached, payload)
        log("zones_downloaded", bytes=len(payload))
    return pl.read_csv(storage.read_bytes(cached)).lazy()


def _stored_meta(paths: storage.Layout, year: int, month: int) -> dict | None:
    uri = paths.raw_meta(year, month)
    return storage.read_json(uri) if storage.exists(uri) else None


def _latest_input_rows(paths: storage.Layout, year: int, month: int) -> int | None:
    """Manifesty jsou append-only, takže předchozí verze téhož měsíce je pořád k mání.
    Rozdíl proti ní se vykazuje jako metrika, ne jako práh -- baseline pro něj nikdo
    nezměřil."""
    names = storage.list_names(paths.runs_dir(year, month))
    if not names:
        return None
    latest = storage.read_json(storage.join(paths.runs_dir(year, month), names[-1]))
    return latest.get("rows", {}).get("input")


def _previous_input_rows(paths: storage.Layout, year: int, month: int) -> int | None:
    previous = date(year, month, 1) - timedelta(days=1)
    rows = _latest_input_rows(paths, previous.year, previous.month)
    if rows is None:
        log("volume_check_skipped", reason="předchozí měsíc není zpracovaný")
    return rows


def _age_days(year: int, month: int) -> int:
    end = date(year + month // 12, month % 12 + 1, 1)
    return (date.today() - end).days


def _git_sha() -> str | None:
    return os.environ.get("GIT_SHA")
