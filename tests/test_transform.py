"""Fixture tvrdí přesná čísla spočítaná ručně. Spadne-li po změně logiky, je to žádoucí:
vynutí vědomé potvrzení, že se publikovaná čísla změnila."""

from dataclasses import replace

import pytest

from app import dq
from app.errors import DataQualityError
from app.transform import OUTPUT_COLUMNS, transform


def result(cfg, trips, zones):
    return transform(trips, zones, dq.rules(cfg, 2025, 1))


def test_kazdy_prumer_ma_svuj_jmenovatel(cfg, trips, zones):
    row = result(cfg, trips, zones).aggregate.filter(location_id=100).to_dicts()[0]

    assert row["trips"] == 10  # storno a prosinec vypadly, jeden řádek je z jiné zóny
    # vzdálenost: 1, 2, 4, 3, 5, 2, 220 (nulová, 300 000 mil a 100 mil za 10 minut ne)
    assert (row["distance_obs"], row["avg_distance_mi"], row["median_distance_mi"]) == (
        7,
        33.857,
        3.0,
    )
    # doba: 5, 10, 20, 15, 30, 10, 10, 240 minut (záporná a 8,5 h se nepočítají)
    assert (row["duration_obs"], row["avg_duration_min"], row["median_duration_min"]) == (
        8,
        42.5,
        12.5,
    )
    # jízdné: 0, 10, 20, 15, 30, 12, 40, 18, 400 -> 545/9; záporné jízdné se nepočítá
    assert (row["fare_obs"], row["avg_fare_usd"]) == (9, 60.56)


def test_penize_zustavaji_i_kdyz_je_mereni_rozbite(cfg, trips, zones):
    row = result(cfg, trips, zones).aggregate.filter(location_id=100).to_dicts()[0]

    # 12 + 24 + 18 + 36 + 14 + 50 + 6 + 20 + 420 + 0; jízda s rozbitým tachometrem je
    # tržba a nezpoplatněná jízda taky, jen nulová
    assert row["yellow_revenue_usd"] == 600.0
    # jen storno -5: nula se nevrací, ta jízda se odjela; prosinec do ledna nepatří vůbec
    assert row["refunds_usd"] == -5.0
    # čistá tržba je součet obou -- hrubá se nikam neztrácí, jen už není headline
    assert row["net_revenue_usd"] == 595.0


def test_karantena_storna_a_publikovane_je_vstup(cfg, trips, zones):
    rows = result(cfg, trips, zones).metrics["rows"]

    assert rows == {"input": 13, "published": 11, "reversed": 1, "rejected": 1, "output": 2}
    # Storna se počítají zvlášť od karantény, ale ze vstupu nesmí zmizet ani jedno.
    assert rows["published"] + rows["reversed"] + rows["rejected"] == rows["input"]


def test_pravidla_se_pocitaji_nezavisle_na_poradi(cfg, trips, zones):
    metrics = result(cfg, trips, zones).metrics

    # storno porušuje negative_fare i reversal; oba počty ho vidí
    assert metrics["rules"] == {
        "out_of_month": 1,
        "implausible_distance": 1,
        "impossible_speed": 2,  # 300 000 mil za půl hodiny i 100 mil za 10 minut
        "duration_over_limit": 1,
        "nonpositive_duration": 1,
        "negative_fare": 2,
        "zero_distance": 1,
        "zero_total": 1,  # jen se počítá, řádek zůstává publikovaný
        "reversal": 2,  # storno i jízda z prosince, ta má taky zápornou útratu
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
    assert (row["distance_obs"], row["avg_distance_mi"]) == (7, 33.857)


def test_stitek_vyrazeneho_radku_je_nejvzacnejsi_pravidlo(cfg, trips, zones):
    excluded = result(cfg, trips, zones).excluded

    # Řádek z prosince je taky protizápis, ale rozhoduje vzácnější pravidlo -- a je to
    # tak správně: do refunds tohohle měsíce nepatří o nic víc než ta jízda sama.
    reasons = sorted(excluded["reject_reason"].to_list())
    assert reasons == ["out_of_month", "reversal"]


def test_vyrazeny_radek_si_nese_hodnotu_kvuli_ktere_vypadl(cfg, trips, zones):
    """Nulování se aplikuje až za oddělením karantény. Kdyby bylo dřív, storno se
    záporným jízdným by v rejects leželo s `fare_amount = null` -- tedy bez toho čísla,
    kvůli kterému tam je. Na reálném lednu 2025 to bylo 62 716 z 63 059 řádků."""
    excluded = result(cfg, trips, zones).excluded

    assert excluded.null_count().sum_horizontal().item() == 0
    storno = excluded.filter(reject_reason="reversal").to_dicts()[0]
    assert (storno["fare_amount"], storno["total_amount"]) == (-5.0, -5.0)


def test_dimenze_se_dojoinovala_vcetne_neznamych_zon(cfg, trips, zones):
    aggregate = result(cfg, trips, zones).aggregate

    assert aggregate.columns == OUTPUT_COLUMNS
    assert aggregate["borough"].to_list() == ["Manhattan", "Unknown"]
    assert aggregate["zone"].null_count() == 0


def test_storna_nepadaji_do_rozpoctu_karanteny(cfg):
    """Proč tenhle test existuje: než se storna oddělila, jedla z `max_reject_ratio` a
    gate padal na tom, že se lidem víc reklamovaly jízdy. Podíl storn je vlastnost
    vendor mixu, ne kvality dat -- a proto má vlastní práh."""
    rows = {"input": 1000, "published": 900, "reversed": 90, "rejected": 10, "output": 5}
    metrics = {"rows": rows, "nulled": {}}

    # 9 % storn přeteče vlastní práh a hlásí storna, ne karanténu
    with pytest.raises(DataQualityError, match="storna 9,00 %|storna 9.00%"):
        dq.gate(metrics, cfg, None)

    # Se zvednutým prahem na storna projde: 90 + 10 řádků je 10 % vstupu, což by přes
    # max_reject_ratio 5 % neprošlo, kdyby se storna do karantény pořád počítala.
    dq.gate(metrics, replace(cfg, max_reversal_ratio=0.20), None)
