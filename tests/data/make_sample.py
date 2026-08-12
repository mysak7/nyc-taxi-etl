"""Vyrobí vzorky, které leží v repu, aby binárky v `tests/data/` nevypadaly jako smetí.

Vzorek 2025-01 je vybraný tak, aby obsahoval patologie (ne prvních N řádků), vzorek
2024-01 slouží k doloženému schema driftu -- má 19 sloupců, `cbd_congestion_fee` přišel
až s congestion pricing v lednu 2025.

    uv run tests/data/make_sample.py 2025-01 --rows 500
    uv run tests/data/make_sample.py 2024-01 --rows 200
"""

import argparse
from pathlib import Path

import polars as pl

URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_{month}.parquet"

parser = argparse.ArgumentParser()
parser.add_argument("month", help="YYYY-MM")
parser.add_argument("--source", help="cesta k parquetu; jinak se stahuje z TLC")
parser.add_argument("--rows", type=int, default=500)
args = parser.parse_args()

frame = pl.read_parquet(args.source or URL.format(month=args.month))
duration = (
    pl.col("tpep_dropoff_datetime") - pl.col("tpep_pickup_datetime")
).dt.total_seconds() / 60

pathologies = {
    "zero_distance": pl.col("trip_distance") <= 0,
    "implausible_distance": pl.col("trip_distance") > 300,
    "impossible_speed": (duration > 1) & (pl.col("trip_distance") / (duration / 60) > 100),
    "negative_fare": pl.col("fare_amount") < 0,
    "nonpositive_total": pl.col("total_amount") <= 0,
    "nonpositive_duration": duration <= 0,
    "long_duration": duration > 480,
    "unknown_zone": pl.col("PULocationID").is_in([264, 265]),
}

parts = [frame.filter(condition).head(20) for condition in pathologies.values()]
parts.append(frame.sample(args.rows - sum(part.height for part in parts), seed=1))
sample = pl.concat(parts).sample(fraction=1.0, shuffle=True, seed=1)

target = Path(__file__).with_name(f"sample_{args.month}.parquet")
sample.write_parquet(target)
print(f"{target.name}: {sample.height} řádků, {sample.width} sloupců")
