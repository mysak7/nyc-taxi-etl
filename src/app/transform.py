"""Čistá transformace: LazyFrame -> (agregace, karanténa, metriky). Žádné I/O, žádné
cesty -- proto se testuje bez disku.

Jeden průchod zdrojem: `scan_parquet` + jeden `collect()`. Lazy je tu kvůli projection
pushdownu (20 sloupců = 467 MB v paměti, 6 potřebných = 146 MB), ne kvůli streamingu.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from .dq import Rule

NEEDED = [
    "tpep_pickup_datetime",
    "tpep_dropoff_datetime",
    "PULocationID",
    "trip_distance",
    "fare_amount",
    "total_amount",
]

OUTPUT_COLUMNS = [
    "date",
    "location_id",
    "borough",
    "zone",
    "service_zone",
    "trips",
    "avg_distance_mi",
    "median_distance_mi",
    "distance_obs",
    "avg_duration_min",
    "median_duration_min",
    "duration_obs",
    "avg_fare_usd",
    "fare_obs",
    "yellow_revenue_usd",
    "refunds_usd",
]


@dataclass(frozen=True)
class Result:
    aggregate: pl.DataFrame
    rejects: pl.DataFrame
    metrics: dict


def transform(trips: pl.LazyFrame, zones: pl.LazyFrame, rules: list[Rule]) -> Result:
    duration = (
        pl.col("tpep_dropoff_datetime") - pl.col("tpep_pickup_datetime")
    ).dt.total_seconds() / 60

    flagged = (
        trips.select(NEEDED)
        .with_columns(duration_min=duration)
        .with_columns([rule.expr.alias(f"v_{rule.name}") for rule in rules])
    )

    # Řádek může porušit víc pravidel; štítek dostane první podle pořadí v `rules`.
    reason = pl.lit(None, dtype=pl.String)
    for rule in reversed([r for r in rules if r.action == "reject"]):
        reason = pl.when(pl.col(f"v_{rule.name}")).then(pl.lit(rule.name)).otherwise(reason)
    flagged = flagged.with_columns(reject_reason=reason)

    nulled_by: dict[str, list[str]] = {}
    for rule in (r for r in rules if r.action != "reject"):
        nulled_by.setdefault(rule.action, []).append(f"v_{rule.name}")
    flagged = flagged.with_columns(
        [
            pl.when(pl.any_horizontal(flags)).then(None).otherwise(pl.col(column)).alias(column)
            for column, flags in nulled_by.items()
        ]
    )

    frame = flagged.collect()  # jediný collect

    published = frame.filter(pl.col("reject_reason").is_null())
    rejects = frame.filter(pl.col("reject_reason").is_not_null())

    aggregate = _aggregate(published).join(
        _refunds(rejects), on=["date", "location_id"], how="full", coalesce=True
    )
    aggregate = aggregate.with_columns(
        pl.col("trips", "distance_obs", "duration_obs", "fare_obs").fill_null(0),
        pl.col("yellow_revenue_usd", "refunds_usd").fill_null(0.0),
    )
    aggregate = (
        aggregate.join(_zones(zones), on="location_id", how="left", validate="m:1")
        .select(OUTPUT_COLUMNS)
        .sort("date", "location_id")
    )

    metrics = {
        "rows": {
            "input": frame.height,
            "published": published.height,
            "rejected": rejects.height,
            "output": aggregate.height,
        },
        "rules": {r.name: int(frame[f"v_{r.name}"].sum()) for r in rules},
        "nulled": {
            column: int(frame.select(pl.any_horizontal(flags).sum()).item())
            for column, flags in nulled_by.items()
        },
    }
    return Result(
        aggregate=aggregate, rejects=rejects.select(NEEDED + ["reject_reason"]), metrics=metrics
    )


def _aggregate(published: pl.DataFrame) -> pl.DataFrame:
    """Každý průměr nese svůj jmenovatel: globálně je jmenovatel vzdálenosti 97 %, ale
    u Newark Airportu 21 % -- jedno číslo za měsíc by to nepopsalo."""
    return published.group_by(
        pl.col("tpep_pickup_datetime").dt.date().alias("date"),
        pl.col("PULocationID").cast(pl.Int32).alias("location_id"),
    ).agg(
        trips=pl.len(),
        avg_distance_mi=pl.col("trip_distance").mean().round(3),
        median_distance_mi=pl.col("trip_distance").median().round(3),
        distance_obs=pl.col("trip_distance").count(),
        avg_duration_min=pl.col("duration_min").mean().round(2),
        median_duration_min=pl.col("duration_min").median().round(2),
        duration_obs=pl.col("duration_min").count(),
        avg_fare_usd=pl.col("fare_amount").mean().round(2),
        fare_obs=pl.col("fare_amount").count(),
        yellow_revenue_usd=pl.col("total_amount").sum().round(2),
    )


def _refunds(rejects: pl.DataFrame) -> pl.DataFrame:
    """Storna nezmizí v karanténě: jejich objem se vykazuje zvlášť, hrubá tržba zůstává
    hrubá. Jízdy mimo zpracovávaný měsíc do partition nepatří vůbec."""
    return (
        rejects.filter(pl.col("reject_reason") == "nonpositive_total")
        .group_by(
            pl.col("tpep_pickup_datetime").dt.date().alias("date"),
            pl.col("PULocationID").cast(pl.Int32).alias("location_id"),
        )
        .agg(refunds_usd=pl.col("total_amount").sum().round(2))
    )


def _zones(zones: pl.LazyFrame) -> pl.DataFrame:
    return (
        zones.rename({"LocationID": "location_id", "Borough": "borough", "Zone": "zone"})
        .select(pl.col("location_id").cast(pl.Int32), "borough", "zone", "service_zone")
        .collect()
    )
