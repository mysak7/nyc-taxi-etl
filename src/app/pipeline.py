"""Orchestrace: co se má udělat (`detect`) a udělání jednoho měsíce (`run_month`).

Stav není nikde zvlášť -- stav *je* to, co je zapsané. `detect` porovná ETag ze zdroje
proti sidecaru u uloženého raw souboru; když sidecar chybí, měsíc se prostě zpracuje
znovu (zápis je idempotentní).
"""

from __future__ import annotations

import os
import secrets
import time
from datetime import UTC, date, datetime

import polars as pl

from . import dq, source, storage
from .config import Config
from .errors import DataQualityError, PermanentError
from .log import bind, log
from .months import label, next_month, parse, previous_month
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
    today = today or date.today()
    cursor = previous_month(today.year, today.month)
    months = []
    for _ in range(cfg.lookback_months):
        months.append(cursor)
        cursor = previous_month(*cursor)
    return months


def month_range(from_month: str | None, to_month: str | None) -> list[tuple[int, int]] | None:
    """Backfill je explicitní rozsah, ne druhý DAG a ne druhá cesta kódem."""
    if not from_month and not to_month:
        return None
    cursor, end = parse(from_month or to_month), parse(to_month or from_month)
    months = []
    while cursor <= end:  # měsíc je pár (year, month), takže se porovnává přímo
        months.append(cursor)
        cursor = next_month(*cursor)
    return months


def detect(cfg: Config, months: list[tuple[int, int]] | None = None, force: bool = False) -> dict:
    paths = layout(cfg)
    candidates = months or window(cfg)
    work, published, gaps = [], [], []

    for year, month in candidates:
        meta = source.head(cfg.month_url(year, month))
        if meta is None:
            log("month_not_published", year=year, month=month)
            continue
        published.append((year, month))

        stored = _stored_meta(paths, year, month)
        has_output = storage.exists(paths.curated_file(year, month))  # na S3 HEAD, ne dvakrát
        if not has_output:
            gaps.append(label(year, month))
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

    newest = max(published) if published else None  # páry se řadí chronologicky
    return {
        "months": work,
        # Co se ptalo, ne jen co z toho vypadlo: den bez práce je taky výsledek a bez
        # tohohle seznamu by se nedal odlišit od dne, kdy se pipeline vůbec nespustila.
        "checked": [label(year, month) for year, month in candidates],
        "source_newest": label(*newest) if newest else None,
        "source_age_days": _age_days(*newest) if newest else None,
        "ingest_gap": gaps,
    }


def check_freshness(cfg: Config, trigger: str = "cli", record: bool = True) -> dict:
    """Zelený DAG nesmí lhát. Dvě různá selhání, dvě různá tvrzení: publikovaný měsíc bez
    výstupu (naše chyba, bez časového prahu) a zdroj, který přestal publikovat.

    Běží na konci každé exekuce, i té bez práce -- proto se tady zapisuje i záznam
    o kontrole. `record=False` je pro čtenáře curated (build stránky), který se ptá na
    totéž, ale nic nespouští a do bucketu nesmí psát."""
    state = detect(cfg)
    problem = _freshness_problem(cfg, state)
    if record:
        _write_check(cfg, state, trigger, problem)
    if problem:
        raise DataQualityError(problem)
    log("freshness_ok", **state)
    return state


def _freshness_problem(cfg: Config, state: dict) -> str | None:
    if state["ingest_gap"]:
        return f"publikované měsíce bez výstupu: {state['ingest_gap']}"
    if state["source_newest"] is None:
        return "v okně není žádný publikovaný měsíc -- změnilo se URL schéma?"
    if state["source_age_days"] > cfg.source_stale_days:
        return (
            f"nejnovější data jsou {state['source_newest']} ({state['source_age_days']} dní);"
            f" práh je {cfg.source_stale_days}"
        )
    return None


def _write_check(cfg: Config, state: dict, trigger: str, problem: str | None) -> None:
    """Den bez nových dat po sobě nenechá manifest, a přesto se běželo: zdroj publikuje
    po měsících, takže většina běhů jen porovná ETagy. Bez tohohle záznamu vypadá takový
    den z curated stejně jako den, kdy scheduler neodpálil nic -- stránka by uměla říct
    jen „poslední data jsou z ledna", ne „ptali jsme se dnes ráno".

    Zapisuje se i k selhání, ještě před vyhozením výjimky: že kontrola proběhla a co
    našla, je informace i (hlavně) když skončila červeně."""
    check_id = new_run_id()  # tentýž tvar jako run_id: řaditelné razítko + token
    document = {
        "schema_version": 1,
        "check_id": check_id,
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "trigger": trigger,
        "app_version": VERSION,
        "git_sha": os.environ.get("GIT_SHA"),  # ARG GIT_SHA v Dockerfile
        "dataset": "yellow",
        "checked": state["checked"],
        "changed": [label(month["year"], month["month"]) for month in state["months"]],
        "source_newest": state["source_newest"],
        "source_age_days": state["source_age_days"],
        "ingest_gap": state["ingest_gap"],
        "status": "failed" if problem else "ok",
        "detail": problem,
    }
    storage.write_json(layout(cfg).check_file(check_id), document)
    log("check_recorded", check_id=check_id, status=document["status"], changed=document["changed"])


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

    trips = storage.scan_parquet(paths.raw_file(year, month))
    schema = dq.check_contract(dict(trips.collect_schema()))
    log("contract_ok", **schema)

    rules = dq.rules(cfg, year, month)
    result = transform(trips, zones, rules)

    # `prev_version_input` = předchozí verze *téhož* měsíce (jen se vykazuje),
    # `previous` = předchozí měsíc (na ten je práh objemu).
    metrics = result.metrics | {
        "rows": result.metrics["rows"]
        | {"prev_version_input": _latest_input_rows(paths, year, month)}
    }
    dq.gate(metrics, cfg, _previous_input_rows(paths, year, month))

    storage.write_parquet(paths.curated_file(year, month), result.aggregate)
    storage.write_parquet(paths.rejects_file(year, month), result.excluded)
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "trigger": trigger,
        "app_version": VERSION,
        "git_sha": os.environ.get("GIT_SHA"),  # ARG GIT_SHA v Dockerfile
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
        **metrics,
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
    rows = _latest_input_rows(paths, *previous_month(year, month))
    if rows is None:
        log("volume_check_skipped", reason="předchozí měsíc není zpracovaný")
    return rows


def _age_days(year: int, month: int) -> int:
    """Stáří měsíce = kolik dní uplynulo od jeho konce."""
    return (date.today() - date(*next_month(year, month), 1)).days
