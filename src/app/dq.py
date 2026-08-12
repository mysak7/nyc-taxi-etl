"""Pravidla jako data. Dvě severity:

ERROR  = kontrakt schématu -> tvrdý pád před transformací (porucha, ne šum).
QUALITY = kvalita řádku -> buď karanténa (není účtenka), nebo vynulování vadného
          pole (řádek i jeho peníze zůstávají).
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from .config import Config
from .errors import DataQualityError

# Sloupce, bez kterých výstup nevznikne. Kontrakt je "existuje a je toho druhu",
# ne "schéma se přesně rovná" -- TLC sloupce přidává (cbd_congestion_fee od 2025-01).
REQUIRED: dict[str, str] = {
    "tpep_pickup_datetime": "temporal",
    "tpep_dropoff_datetime": "temporal",
    "PULocationID": "integer",
    "trip_distance": "numeric",
    "fare_amount": "numeric",
    "total_amount": "numeric",
}

# Baseline pro hlášení driftu (schéma 2025-01). Rozdíl proti němu není důvod k pádu,
# ale patří do manifestu.
BASELINE_COLUMNS = frozenset(
    [
        "VendorID",
        "tpep_pickup_datetime",
        "tpep_dropoff_datetime",
        "passenger_count",
        "trip_distance",
        "RatecodeID",
        "store_and_fwd_flag",
        "PULocationID",
        "DOLocationID",
        "payment_type",
        "fare_amount",
        "extra",
        "mta_tax",
        "tip_amount",
        "tolls_amount",
        "improvement_surcharge",
        "total_amount",
        "congestion_surcharge",
        "Airport_fee",
        "cbd_congestion_fee",
    ]
)


@dataclass(frozen=True)
class Rule:
    name: str
    expr: pl.Expr  # True = porušeno
    action: str  # "reject" nebo jméno sloupce, který se vynuluje


def check_contract(schema: dict[str, pl.DataType]) -> dict:
    """Vrátí popis schématu do manifestu, nebo padne, chybí-li povinný sloupec."""
    missing = [c for c in REQUIRED if c not in schema]
    if missing:
        raise DataQualityError(f"zdroj neobsahuje povinné sloupce: {missing}")

    wrong = []
    for column, kind in REQUIRED.items():
        dtype = schema[column]
        ok = dtype.is_temporal() if kind == "temporal" else dtype.is_numeric()
        if kind == "integer":
            ok = dtype.is_integer()
        if not ok:
            wrong.append(f"{column}: {dtype} není {kind}")
    if wrong:
        raise DataQualityError(f"nesedí typy povinných sloupců: {wrong}")

    observed = set(schema)
    return {
        "columns": len(schema),
        "extra": sorted(observed - BASELINE_COLUMNS),
        "missing_optional": sorted(BASELINE_COLUMNS - observed),
    }


def rules(cfg: Config, year: int, month: int) -> list[Rule]:
    """Pořadí = od nejvzácnějšího pravidla k nejčastějšímu (naměřeno na 2025-01), aby
    vzácnou patologii v `reject_reason` nespolklo objemové pravidlo."""
    start = pl.datetime(year, month, 1)
    end = pl.datetime(year + month // 12, month % 12 + 1, 1)
    pickup = pl.col("tpep_pickup_datetime")
    return [
        # Není kvalita, ale partitioning: lednový soubor obsahuje 22 jízd z prosince
        # a února. Jedna partition = jeden zdrojový měsíc (idempotence přepisu).
        Rule("out_of_month", (pickup < start) | (pickup >= end), "reject"),
        Rule(
            "implausible_distance", pl.col("trip_distance") > cfg.max_distance_mi, "trip_distance"
        ),
        Rule("duration_over_limit", pl.col("duration_min") > cfg.max_duration_min, "duration_min"),
        Rule("nonpositive_duration", pl.col("duration_min") <= 0, "duration_min"),
        # Záporné jízdné u zaplacené jízdy je rozbité pole, ne fiktivní jízda.
        Rule("negative_fare", pl.col("fare_amount") < 0, "fare_amount"),
        Rule("zero_distance", pl.col("trip_distance") <= 0, "trip_distance"),
        # Není účtenka -> nic nepřiteklo. Storna se vykazují v refunds_usd.
        Rule("nonpositive_total", pl.col("total_amount") <= 0, "reject"),
    ]


def gate(metrics: dict, cfg: Config, previous_input_rows: int | None) -> None:
    """Tvrdé prahy. Retry je nezachrání, proto DataQualityError (fail-fast)."""
    rows = metrics["rows"]
    if rows["input"] == 0:
        raise DataQualityError("zdroj má nula řádků")

    ratio = rows["rejected"] / rows["input"]
    if ratio > cfg.max_reject_ratio:
        raise DataQualityError(f"karanténa {ratio:.2%} > práh {cfg.max_reject_ratio:.0%}")

    for column, threshold in (
        ("trip_distance", cfg.max_null_ratio_distance),
        ("duration_min", cfg.max_null_ratio_duration),
    ):
        nulled = metrics["nulled"].get(column, 0) / rows["input"]
        if nulled > threshold:
            raise DataQualityError(f"vynulováno {column} {nulled:.2%} > práh {threshold:.0%}")

    if previous_input_rows:
        delta = rows["input"] / previous_input_rows - 1
        if abs(delta) > cfg.max_volume_delta:
            raise DataQualityError(
                f"objem {delta:+.1%} proti předchozímu měsíci ({previous_input_rows:,} řádků)"
            )
