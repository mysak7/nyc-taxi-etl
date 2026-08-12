"""Fixture tvrdí přesná čísla spočítaná ručně. Spadne-li po změně logiky, je to žádoucí:
vynutí vědomé potvrzení, že se publikovaná čísla změnila."""

from app import dq
from app.transform import OUTPUT_COLUMNS, transform


def result(cfg, trips, zones):
    return transform(trips, zones, dq.rules(cfg, 2025, 1))


def test_kazdy_prumer_ma_svuj_jmenovatel(cfg, trips, zones):
    row = result(cfg, trips, zones).aggregate.filter(location_id=100).to_dicts()[0]

    assert row["trips"] == 7  # tři řádky vypadly do karantény, jeden je z jiné zóny
    # vzdálenost: 2, 4, 3, 5, 2 (nulová a 300 000 mil se nepočítají)
    assert (row["distance_obs"], row["avg_distance_mi"], row["median_distance_mi"]) == (5, 3.2, 3.0)
    # doba: 10, 20, 15, 30, 10 minut (záporná a 8,5 h se nepočítají)
    assert (row["duration_obs"], row["avg_duration_min"], row["median_duration_min"]) == (
        5,
        17.0,
        15.0,
    )
    # jízdné: 10, 20, 15, 30, 12, 40 -> 127/6; záporné jízdné se nepočítá
    assert (row["fare_obs"], row["avg_fare_usd"]) == (6, 21.17)


def test_penize_zustavaji_i_kdyz_je_mereni_rozbite(cfg, trips, zones):
    row = result(cfg, trips, zones).aggregate.filter(location_id=100).to_dicts()[0]

    # 12 + 24 + 18 + 36 + 14 + 50 + 6; jízda s rozbitým tachometrem je pořád tržba
    assert row["yellow_revenue_usd"] == 160.0
    # storno -5 a nezpoplatněná jízda 0; jízda z prosince se do ledna nepočítá vůbec
    assert row["refunds_usd"] == -5.0


def test_karantena_plus_publikovane_je_vstup(cfg, trips, zones):
    metrics = result(cfg, trips, zones).metrics

    assert metrics["rows"] == {"input": 11, "published": 8, "rejected": 3, "output": 2}
    assert metrics["rows"]["published"] + metrics["rows"]["rejected"] == metrics["rows"]["input"]


def test_pravidla_se_pocitaji_nezavisle_na_poradi(cfg, trips, zones):
    metrics = result(cfg, trips, zones).metrics

    # storno porušuje negative_fare i nonpositive_total; oba počty ho vidí
    assert metrics["rules"] == {
        "out_of_month": 1,
        "implausible_distance": 1,
        "duration_over_limit": 1,
        "nonpositive_duration": 1,
        "negative_fare": 2,
        "zero_distance": 1,
        "nonpositive_total": 3,
    }
    assert metrics["nulled"] == {"trip_distance": 2, "duration_min": 2, "fare_amount": 2}


def test_stitek_karanteny_je_nejvzacnejsi_pravidlo(cfg, trips, zones):
    rejects = result(cfg, trips, zones).rejects

    # řádek z prosince má i nekladnou útratu, ale rozhoduje vzácnější pravidlo
    reasons = sorted(rejects["reject_reason"].to_list())
    assert reasons == ["nonpositive_total", "nonpositive_total", "out_of_month"]


def test_dimenze_se_dojoinovala_vcetne_neznamych_zon(cfg, trips, zones):
    aggregate = result(cfg, trips, zones).aggregate

    assert aggregate.columns == OUTPUT_COLUMNS
    assert aggregate["borough"].to_list() == ["Manhattan", "Unknown"]
    assert aggregate["zone"].null_count() == 0
