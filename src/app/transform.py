"""Čistá transformace: LazyFrame -> (agregace, vyřazené řádky, metriky). Žádné I/O,
žádné cesty -- proto se testuje bez disku.

Jeden průchod zdrojem: `scan_parquet` + jeden `collect()`. Lazy je tu kvůli projection
pushdownu (20 sloupců = 467 MB v paměti, 6 potřebných = 146 MB), ne kvůli streamingu.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from .dq import REVERSAL, ROW_ACTIONS, Rule

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
    "net_revenue_usd",
]


@dataclass(frozen=True)
class Result:
    aggregate: pl.DataFrame
    excluded: pl.DataFrame  # karanténa i storna, rozlišené v `reject_reason`
    metrics: dict


def transform(trips: pl.LazyFrame, zones: pl.LazyFrame, rules: list[Rule]) -> Result:
    duration = (
        pl.col("tpep_dropoff_datetime") - pl.col("tpep_pickup_datetime")
    ).dt.total_seconds() / 60

    nulled_by: dict[str, list[str]] = {}
    for rule in (r for r in rules if r.nullifies):
        nulled_by.setdefault(rule.action, []).append(f"v_{rule.name}")

    frame = (
        trips.select(NEEDED)
        .with_columns(duration_min=duration)
        .with_columns([rule.expr.alias(f"v_{rule.name}") for rule in rules])
        .with_columns(reject_reason=_label(rules))
        .collect()  # jediný collect
    )

    is_published = pl.col("reject_reason").is_null()
    is_reversal = pl.col("reject_reason").is_in([r.name for r in rules if r.action == REVERSAL])

    # Karanténa i storna jdou do rejects s *původními* hodnotami: řádek vypadl kvůli
    # nějakému číslu a bez toho čísla nejde říct proč. Nulování se proto aplikuje až
    # tady, jen na řádky, ze kterých se počítá výstup.
    excluded = frame.filter(~is_published)
    kept = frame.filter(is_published | is_reversal).with_columns(
        [
            pl.when(pl.any_horizontal(flags)).then(None).otherwise(pl.col(column)).alias(column)
            for column, flags in nulled_by.items()
        ]
    )

    aggregate = (
        _aggregate(kept, is_published, is_reversal)
        # Ze zaokrouhlených sloupců, ne z hrubého součtu: kdo si ve výstupu ověří
        # `yellow_revenue_usd + refunds_usd == net_revenue_usd`, musí dostat rovnost.
        .with_columns(
            net_revenue_usd=(pl.col("yellow_revenue_usd") + pl.col("refunds_usd")).round(2)
        )
        .join(_zones(zones), on="location_id", how="left", validate="m:1")
        .select(OUTPUT_COLUMNS)
        .sort("date", "location_id")
    )

    reversed_rows = int(excluded.select(is_reversal.sum()).item())
    metrics = {
        "rows": {
            "input": frame.height,
            "published": frame.height - excluded.height,
            "reversed": reversed_rows,
            "rejected": excluded.height - reversed_rows,
            "output": aggregate.height,
        },
        # Nezávisle na štítku: řádek může porušit víc pravidel a každé se přizná ke svému
        # počtu, takže čísla v manifestu nezávisí na pořadí v `rules`.
        "rules": {r.name: int(frame[f"v_{r.name}"].sum()) for r in rules},
        "nulled": {
            column: int(frame.select(pl.any_horizontal(flags).sum()).item())
            for column, flags in nulled_by.items()
        },
    }
    return Result(
        aggregate=aggregate, excluded=excluded.select(NEEDED + ["reject_reason"]), metrics=metrics
    )


def _label(rules: list[Rule]) -> pl.Expr:
    """Řádek může porušit víc pravidel; štítek dostane první podle pořadí v `rules`.
    Karanténa i storno berou celý řádek, takže soutěží o tentýž štítek -- a pořadí
    rozhoduje správně: storno jízdy z prosince je `out_of_month`, do refunds tohohle
    měsíce nepatří o nic víc než ta jízda sama."""
    return pl.coalesce(
        [
            pl.when(pl.col(f"v_{rule.name}")).then(pl.lit(rule.name))
            for rule in rules
            if rule.action in ROW_ACTIONS
        ]
    )


def _aggregate(kept: pl.DataFrame, published: pl.Expr, reversal: pl.Expr) -> pl.DataFrame:
    """Jízdy a storna se agregují jedním průchodem: jsou to řádky téhož dne a téže zóny,
    jen se počítají do jiných sloupců. Dřív to byly dva `group_by` spojené full outer
    joinem a `fill_null` -- ta samá věta, jen delší.

    Každý průměr nese svůj jmenovatel: globálně je jmenovatel vzdálenosti 97 %, ale
    u Newark Airportu 21 % -- jedno číslo za měsíc by to nepopsalo. Zóna, kde v ten den
    bylo *jen* storno, tak vyjde s `trips = 0` a průměry `null`; peníze má.
    """
    return kept.group_by(
        pl.col("tpep_pickup_datetime").dt.date().alias("date"),
        pl.col("PULocationID").cast(pl.Int32).alias("location_id"),
    ).agg(
        trips=published.sum(),
        # `filter(published)` u každé metriky: storno není jízda, takže do žádného
        # průměru ani jmenovatele nepatří. Bere se od něj jedině `refunds_usd`.
        avg_distance_mi=pl.col("trip_distance").filter(published).mean().round(3),
        median_distance_mi=pl.col("trip_distance").filter(published).median().round(3),
        distance_obs=pl.col("trip_distance").filter(published).count(),
        avg_duration_min=pl.col("duration_min").filter(published).mean().round(2),
        median_duration_min=pl.col("duration_min").filter(published).median().round(2),
        duration_obs=pl.col("duration_min").filter(published).count(),
        avg_fare_usd=pl.col("fare_amount").filter(published).mean().round(2),
        fare_obs=pl.col("fare_amount").filter(published).count(),
        yellow_revenue_usd=pl.col("total_amount").filter(published).sum().round(2),
        refunds_usd=pl.col("total_amount").filter(reversal).sum().round(2),
    )


def _zones(zones: pl.LazyFrame) -> pl.DataFrame:
    return (
        zones.rename({"LocationID": "location_id", "Borough": "borough", "Zone": "zone"})
        .select(pl.col("location_id").cast(pl.Int32), "borough", "zone", "service_zone")
        .collect()
    )
