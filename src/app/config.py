from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import get_type_hints

DEFAULTS = Path(__file__).with_name("config.toml")


@dataclass(frozen=True)
class Config:
    """Každé pole je schválně jen `str`/`int`/`float`: hodnota z TOML i z env se pak
    převede tím, že se zavolá typ pole. `Optional` nebo vnořená struktura by tuhle
    jednu řádku rozbila -- konfigurace je plochá záměrně."""

    url_template: str
    zones_url: str
    raw_uri: str
    curated_uri: str
    rejects_uri: str
    lookback_months: int
    source_stale_days: int
    max_speed_mph: float
    max_distance_mi: float
    max_duration_min: float
    max_reject_ratio: float
    max_reversal_ratio: float
    max_volume_delta: float
    max_null_ratio_distance: float
    max_null_ratio_duration: float

    @classmethod
    def load(cls, path: str | Path | None = None, **overrides) -> Config:
        data = tomllib.loads(Path(path or DEFAULTS).read_text())
        types = get_type_hints(cls)  # rozbalí stringové anotace z `from __future__`
        for name in types:
            env = os.environ.get(f"APP_{name.upper()}")
            if env is not None:
                data[name] = env
        data.update({k: v for k, v in overrides.items() if v is not None})
        unknown = set(data) - set(types)
        if unknown:
            raise ValueError(f"neznámé položky konfigurace: {sorted(unknown)}")
        return cls(**{name: types[name](value) for name, value in data.items()})

    def thresholds(self) -> dict[str, float]:
        """Prahy, které při běhu platily -- jdou do manifestu, jinak staré manifesty
        nejde interpretovat po jejich změně."""
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name.startswith("max_")}

    def month_url(self, year: int, month: int) -> str:
        return self.url_template.format(year=year, month=month)
