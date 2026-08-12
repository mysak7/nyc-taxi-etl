from datetime import datetime

import polars as pl
import pytest

from app.config import Config


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config.load(
        raw_uri=str(tmp_path / "raw"),
        curated_uri=str(tmp_path / "curated"),
        rejects_uri=str(tmp_path / "rejects"),
    )


@pytest.fixture
def zones() -> pl.LazyFrame:
    return pl.LazyFrame(
        {
            "LocationID": [100, 264],
            "Borough": ["Manhattan", "Unknown"],
            "Zone": ["Midtown", "NV"],
            "service_zone": ["Yellow Zone", "N/A"],
        }
    ).cast({"LocationID": pl.Int64})


def trip(pickup: str, dropoff: str, distance: float, fare: float, total: float, zone: int = 100):
    return {
        "tpep_pickup_datetime": datetime.fromisoformat(pickup),
        "tpep_dropoff_datetime": datetime.fromisoformat(dropoff),
        "PULocationID": zone,
        "trip_distance": distance,
        "fare_amount": fare,
        "total_amount": total,
    }


@pytest.fixture
def trips() -> pl.LazyFrame:
    """Jeden řádek = jeden případ. Čísla v testech jsou spočítaná ručně z těchto hodnot,
    takže test spadne, když se změní logika -- a to je jeho účel."""
    return pl.LazyFrame(
        [
            trip("2025-01-05T10:00", "2025-01-05T10:10", 2.0, 10.0, 12.0),  # v pořádku
            trip("2025-01-05T11:00", "2025-01-05T11:20", 4.0, 20.0, 24.0),  # v pořádku
            trip("2025-01-05T12:00", "2025-01-05T12:15", 0.0, 15.0, 18.0),  # nulová vzdálenost
            trip("2025-01-05T13:00", "2025-01-05T13:30", 300000.0, 30.0, 36.0),  # kolem světa
            trip("2025-01-05T14:00", "2025-01-05T13:50", 3.0, 12.0, 14.0),  # dropoff před pickupem
            trip("2025-01-05T15:00", "2025-01-05T23:30", 5.0, 40.0, 50.0),  # 8,5 hodiny
            trip("2025-01-05T16:00", "2025-01-05T16:10", 2.0, -8.0, 6.0),  # rozbité jízdné
            trip("2025-01-05T17:00", "2025-01-05T17:10", 1.0, -5.0, -5.0),  # storno
            trip("2025-01-05T18:00", "2025-01-05T18:05", 1.0, 0.0, 0.0),  # nezpoplatněná jízda
            trip("2024-12-31T23:00", "2024-12-31T23:10", 1.0, 5.0, -6.0),  # jiný měsíc
            trip("2025-01-05T19:00", "2025-01-05T19:20", 6.0, 25.0, 30.0, zone=264),  # Unknown
        ],
        schema_overrides={"PULocationID": pl.Int64},
    )
