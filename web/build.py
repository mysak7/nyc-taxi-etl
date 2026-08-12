"""Postaví statickou stránku z curated vrstvy.

    uv run python web/build.py                                  # z ./data/curated
    APP_CURATED_URI=s3://bucket/curated uv run python web/build.py

Čte přes `storage`, takže lokální adresář i S3 jsou tentýž parametr -- stejně jako
pro pipeline samotnou. Výstup je jeden soubor `web/dist/index.html` se zapečenými daty:
celá historie je jednotky MB, takže agregát pro stránku se vejde do stovek kB a nic
za běhu se nedotahuje. Žádné API, žádná databáze, nic, co by běželo mezi zobrazeními.

Rozsah dat ve stránce je záměrně menší než curated: denní řady a top zóny, ne všechny
řádky. Kdo chce celý výstup, čte parquet.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from app import pipeline, storage  # noqa: E402
from app.config import Config  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TOP_ZONES = 25

# Prahy a okna do sekce "The pipeline". Berou se ze stejné konfigurace jako pipeline,
# aby stránka nemohla tvrdit jiná čísla, než podle kterých se běh doopravdy řídí.
CONFIG_KEYS = (
    "lookback_months",
    "source_stale_days",
    "max_speed_mph",
    "max_distance_mi",
    "max_duration_min",
    "max_reject_ratio",
    "max_volume_delta",
    "max_null_ratio_distance",
    "max_null_ratio_duration",
)


def partitions(layout: storage.Layout) -> list[tuple[int, int]]:
    """Které partition v curated existují. Sidecar `_runs` je přeskočený -- partition
    je adresář s parquetem, ne s manifesty."""
    base = storage.join(layout.curated_uri, "dataset=yellow")
    found = []
    for year_dir in storage.list_prefixes(base):
        if not year_dir.startswith("year="):
            continue
        year = int(year_dir.removeprefix("year="))
        for month_dir in storage.list_prefixes(storage.join(base, year_dir)):
            if month_dir.startswith("month="):
                found.append((year, int(month_dir.removeprefix("month="))))
    return sorted(found)


def read_runs(layout: storage.Layout, year: int, month: int) -> list[dict]:
    """Manifesty partition, nejnovější první. Append-only: partition se přepisuje,
    manifesty přibývají."""
    runs_dir = layout.runs_dir(year, month)
    runs = [
        storage.read_json(storage.join(runs_dir, name))
        for name in storage.list_names(runs_dir)
        if name.endswith(".json")
    ]
    return sorted(runs, key=lambda r: r["run_id"], reverse=True)


def month_payload(layout: storage.Layout, year: int, month: int) -> dict:
    frame = storage.scan_parquet(layout.curated_file(year, month)).collect()

    daily = (
        frame.group_by("date")
        .agg(
            pl.col("trips").sum().alias("trips"),
            pl.col("yellow_revenue_usd").sum().round(2).alias("revenue"),
            pl.col("refunds_usd").sum().round(2).alias("refunds"),
        )
        .sort("date")
    )

    # Průměr vzdálenosti se agreguje vážený svým jmenovatelem; medián agregovat nelze,
    # bere se medián denních mediánů a stránka ho tak i popisuje.
    zones = (
        frame.group_by("location_id", "borough", "zone")
        .agg(
            pl.col("trips").sum().alias("trips"),
            pl.col("yellow_revenue_usd").sum().round(0).alias("revenue"),
            pl.col("distance_obs").sum().alias("distance_obs"),
            (pl.col("avg_distance_mi") * pl.col("distance_obs")).sum().alias("_dist"),
            pl.col("median_distance_mi").median().round(2).alias("median_distance_mi"),
            (pl.col("avg_fare_usd") * pl.col("fare_obs")).sum().alias("_fare"),
            pl.col("fare_obs").sum().alias("fare_obs"),
        )
        .with_columns(
            avg_distance_mi=(pl.col("_dist") / pl.col("distance_obs")).round(2),
            avg_fare_usd=(pl.col("_fare") / pl.col("fare_obs")).round(2),
            coverage=(pl.col("distance_obs") / pl.col("trips")).round(4),
        )
        .drop("_dist", "_fare")
        .sort("trips", descending=True)
    )

    return {
        "key": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "rows": frame.height,
        "trips": int(frame["trips"].sum()),
        "revenue": round(float(frame["yellow_revenue_usd"].sum()), 2),
        "refunds": round(float(frame["refunds_usd"].sum()), 2),
        "runs": read_runs(layout, year, month),
        "daily": daily.to_dicts(),
        "zones": zones.head(TOP_ZONES).to_dicts(),
        "zones_total": zones.height,
    }


def build() -> pathlib.Path:
    cfg = Config.load()
    layout = pipeline.layout(cfg)

    months = [month_payload(layout, year, month) for year, month in partitions(layout)]
    if not months:
        raise SystemExit(f"v {layout.curated_uri} nejsou žádné partition")

    payload = {
        "dataset": "yellow",
        "generated_at": date.today().isoformat(),
        # Bez URI: jméno bucketu nese číslo AWS účtu a stránka je veřejná.
        "source": {
            "store": "S3" if layout.curated_uri.startswith("s3://") else "disk",
            "region": os.environ.get("AWS_REGION", "eu-central-1"),
        },
        "config": {key: getattr(cfg, key) for key in CONFIG_KEYS},
        "freshness": pipeline.check_freshness(cfg),
        "months": months,
    }

    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    if "</script" in encoded:
        raise SystemExit("payload obsahuje </script, stránka by se rozpadla")

    out = HERE / "dist" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text((HERE / "template.html").read_text().replace("__PAYLOAD__", encoded))

    print(
        f"{out} · {out.stat().st_size / 1024:.0f} kB · {len(months)} partition"
        f" · {sum(len(m['runs']) for m in months)} manifestů"
        f" · {months[0]['key']} → {months[-1]['key']}"
    )
    return out


if __name__ == "__main__":
    build()
