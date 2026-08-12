"""Fixture tvrdí přesná čísla spočítaná ručně. Spadne-li po změně logiky, je to žádoucí:
vynutí vědomé potvrzení, že se publikovaná čísla změnila."""

from app import dq
from app.transform import OUTPUT_COLUMNS, transform


def result(cfg, trips, zones):
    return transform(trips, zones, dq.rules(cfg, 2025, 1))


def test_kazdy_prumer_ma_svuj_jmenovatel(cfg, trips, zones):
    row = result(cfg, trips, zones).aggregate.filter(location_id=100).to_dicts()[0]

    assert row["trips"] == 9  # tři řádky vypadly do karantény, jeden je z jiné zóny
    # vzdálenost: 2, 4, 3, 5, 2, 220 (nulová, 300 000 mil a 100 mil za 10 minut ne)
    assert (row["distance_obs"], row["avg_distance_mi"], row["median_distance_mi"]) == (
        6,
        39.333,
        3.5,
    )
    # doba: 10, 20, 15, 30, 10, 10, 240 minut (záporná a 8,5 h se nepočítají)
    assert (row["duration_obs"], row["avg_duration_min"], row["median_duration_min"]) == (
        7,
        47.86,
        15.0,
    )
    # jízdné: 10, 20, 15, 30, 12, 40, 18, 400 -> 545/8; záporné jízdné se nepočítá
    assert (row["fare_obs"], row["avg_fare_usd"]) == (8, 68.12)


def test_penize_zustavaji_i_kdyz_je_mereni_rozbite(cfg, trips, zones):
    row = result(cfg, trips, zones).aggregate.filter(location_id=100).to_dicts()[0]

    # 12 + 24 + 18 + 36 + 14 + 50 + 6 + 20 + 420; jízda s rozbitým tachometrem je tržba
    assert row["yellow_revenue_usd"] == 600.0
    # storno -5 a nezpoplatněná jízda 0; jízda z prosince se do ledna nepočítá vůbec
    assert row["refunds_usd"] == -5.0


def test_karantena_plus_publikovane_je_vstup(cfg, trips, zones):
    metrics = result(cfg, trips, zones).metrics

    assert metrics["rows"] == {"input": 13, "published": 10, "rejected": 3, "output": 2}
    assert metrics["rows"]["published"] + metrics["rows"]["rejected"] == metrics["rows"]["input"]


def test_pravidla_se_pocitaji_nezavisle_na_poradi(cfg, trips, zones):
    metrics = result(cfg, trips, zones).metrics

    # storno porušuje negative_fare i nonpositive_total; oba počty ho vidí
    assert metrics["rules"] == {
        "out_of_month": 1,
        "implausible_distance": 1,
        "impossible_speed": 2,  # 300 000 mil za půl hodiny i 100 mil za 10 minut
        "duration_over_limit": 1,
        "nonpositive_duration": 1,
        "negative_fare": 2,
        "zero_distance": 1,
        "nonpositive_total": 3,
    }
    assert metrics["nulled"] == {"trip_distance": 3, "duration_min": 2, "fare_amount": 2}


def test_dalkova_jizda_neni_vada_a_vada_pod_prahem_neprojde(cfg, trips, zones):
    """Vzdálenost se posuzuje podle nepoměru k době, ne podle velikosti. 220 mil za
    4 hodiny je jízda a musí se započítat; 100 mil za 10 minut je rozbitý tachometr,
    i když je pod magnitudovým prahem."""
    out = result(cfg, trips, zones)

    assert out.metrics["rules"]["impossible_speed"] == 2
    assert out.metrics["rules"]["implausible_distance"] == 1  # jen 300 000 mil

    # Se starým prahem na magnitudu vycházelo (5, 3.2): 220 mil chybělo v jmenovateli
    # a 100 mil za 10 minut naopak průměr táhlo nahoru.
    row = out.aggregate.filter(location_id=100).to_dicts()[0]
    assert (row["distance_obs"], row["avg_distance_mi"]) == (6, 39.333)


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
