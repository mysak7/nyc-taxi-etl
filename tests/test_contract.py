"""Kontrakt schématu na doloženém driftu: `cbd_congestion_fee` v 2024-01 chybí, od
2025-01 je. Vzorek v repu je zmrazený, takže sám drift nechytí -- to umí jedině kontrola
za běhu; tenhle test dokazuje, že ta kontrola na skutečném driftu funguje.

Reálný vzorek netvrdí žádné konkrétní číslo, jen invarianty. Je proto bezúdržbový při
změnách logiky a spadne, když se rozbije kontrakt.
"""

import polars as pl
import pytest

from app import dq
from app.errors import DataQualityError
from app.transform import transform

SAMPLE_2025 = "tests/data/sample_2025-01.parquet"
SAMPLE_2024 = "tests/data/sample_2024-01.parquet"
ZONES = "tests/data/taxi_zone_lookup.csv"


def schema_of(path: str) -> dict:
    return dict(pl.scan_parquet(path).collect_schema())


def test_novejsi_schema_projde_bez_rozdilu():
    assert dq.check_contract(schema_of(SAMPLE_2025)) == {
        "columns": 20,
        "extra": [],
        "missing_optional": [],
    }


def test_starsi_schema_projde_a_rozdil_je_videt():
    report = dq.check_contract(schema_of(SAMPLE_2024))

    assert report["columns"] == 19
    assert report["missing_optional"] == ["cbd_congestion_fee"]  # congestion pricing od 2025-01


def test_novy_sloupec_neni_duvod_k_padu():
    schema = schema_of(SAMPLE_2025) | {"tlc_novy_sloupec": pl.Int64}

    assert dq.check_contract(schema)["extra"] == ["tlc_novy_sloupec"]


def test_chybejici_povinny_sloupec_padne_pred_transformaci():
    schema = schema_of(SAMPLE_2025)
    del schema["trip_distance"]

    with pytest.raises(DataQualityError, match="trip_distance"):
        dq.check_contract(schema)


def test_spatny_typ_povinneho_sloupce_padne():
    schema = schema_of(SAMPLE_2025) | {"total_amount": pl.String}

    with pytest.raises(DataQualityError, match="total_amount"):
        dq.check_contract(schema)


@pytest.mark.parametrize(
    ("sample", "year", "month"), [(SAMPLE_2025, 2025, 1), (SAMPLE_2024, 2024, 1)]
)
def test_realny_vzorek_drzi_invarianty(cfg, sample, year, month):
    result = transform(pl.scan_parquet(sample), pl.scan_csv(ZONES), dq.rules(cfg, year, month))
    rows, aggregate = result.metrics["rows"], result.aggregate

    assert rows["published"] + rows["rejected"] == rows["input"]
    assert aggregate.height > 0
    assert aggregate["yellow_revenue_usd"].is_finite().all()
    assert aggregate["borough"].null_count() == 0  # každé location_id se dojoinovalo
    assert (aggregate["distance_obs"] <= aggregate["trips"]).all()
    assert (aggregate["refunds_usd"] <= 0).all()
