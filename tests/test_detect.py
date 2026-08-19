"""Detekce práce je celý provozní příběh řešení: zdroj publikuje s lagem 26-85 dní
a soubory zpětně přepisuje. Testuje se bez sítě -- `source.head` je jediné místo s HTTP.
"""

from datetime import date

import polars as pl
import pytest

from app import months, pipeline, source, storage
from app.errors import DataQualityError


@pytest.fixture
def published(monkeypatch):
    """Klíč je (year, month) -> ETag; co v mapě není, vrací 403 (není publikováno)."""
    state: dict[tuple[int, int], str] = {}

    def fake_head(url: str):
        month = url.rsplit("_", 1)[-1].removesuffix(".parquet")
        year, month = (int(part) for part in month.split("-"))
        if (year, month) not in state:
            return None
        return source.SourceMeta(url=url, etag=state[(year, month)], last_modified=None, bytes=1)

    monkeypatch.setattr(source, "head", fake_head)
    return state


def store(cfg, year, month, etag):
    """Zapíše to, co by po sobě zanechal úspěšný běh: raw sidecar + výstupní partition."""
    paths = pipeline.layout(cfg)
    storage.write_json(paths.raw_meta(year, month), {"etag": etag})
    storage.write_parquet(paths.curated_file(year, month), pl.DataFrame({"trips": [1]}))


def test_okno_konci_u_predchoziho_mesice(cfg):
    months = pipeline.window(cfg, today=date(2026, 8, 12))

    assert months[0] == (2026, 7)  # aktuální měsíc zdroj publikovat nemůže
    assert months[-1] == (2026, 2)  # šest měsíců pokryje lag i dávkový restatement
    assert len(months) == cfg.lookback_months


def test_nepublikovany_mesic_neni_prace_ani_chyba(cfg, published):
    result = pipeline.detect(cfg, months=[(2026, 6), (2026, 7)])

    assert result["months"] == []
    assert result["source_newest"] is None


def test_novy_mesic_je_prace(cfg, published):
    published[(2026, 5)] = "etag-a"

    assert pipeline.detect(cfg, months=[(2026, 5)])["months"] == [
        {"year": 2026, "month": 5, "etag": "etag-a", "reason": "new"}
    ]


def test_beze_zmeny_etagu_se_nedela_nic(cfg, published):
    published[(2026, 5)] = "etag-a"
    store(cfg, 2026, 5, "etag-a")

    result = pipeline.detect(cfg, months=[(2026, 5)])

    assert result["months"] == []
    assert result["ingest_gap"] == []


def test_zpetny_restatement_se_pozna_z_etagu(cfg, published):
    published[(2026, 5)] = "etag-b"
    store(cfg, 2026, 5, "etag-a")

    months = pipeline.detect(cfg, months=[(2026, 5)])["months"]

    assert [(m["month"], m["reason"]) for m in months] == [(5, "etag_changed")]


def test_chybejici_vystup_je_prace_i_kdyz_etag_sedi(cfg, published):
    published[(2026, 5)] = "etag-a"
    storage.write_json(pipeline.layout(cfg).raw_meta(2026, 5), {"etag": "etag-a"})

    result = pipeline.detect(cfg, months=[(2026, 5)])

    assert [m["reason"] for m in result["months"]] == ["missing_output"]
    assert result["ingest_gap"] == ["2026-05"]


def test_force_ignoruje_etag_kvuli_backfillu(cfg, published):
    published[(2026, 5)] = "etag-a"
    store(cfg, 2026, 5, "etag-a")

    months = pipeline.detect(cfg, months=[(2026, 5)], force=True)["months"]

    assert [m["reason"] for m in months] == ["force"]


def test_backfill_rozsah_se_rozbali_na_mesice():
    assert pipeline.month_range("2024-11", "2025-02") == [
        (2024, 11),
        (2024, 12),
        (2025, 1),
        (2025, 2),
    ]
    assert pipeline.month_range(None, None) is None


def test_mesicni_aritmetika_drzi_na_prelomu_roku(cfg):
    """Jediné místo, kde se počítá „o měsíc dál" -- dřív to byly tři různé triky
    s `timedelta` na čtyřech místech a přelom roku každý řešil jinak."""
    assert months.next_month(2025, 12) == (2026, 1)
    assert months.previous_month(2025, 1) == (2024, 12)
    assert pipeline.window(cfg, today=date(2025, 1, 15))[:2] == [(2024, 12), (2024, 11)]


def test_zeleny_dag_nelze_kdyz_zdroj_ma_data_ktera_nemame(cfg, published):
    published[(2026, 5)] = "etag-a"

    with pytest.raises(DataQualityError, match="bez výstupu"):
        pipeline.check_freshness(cfg)


def test_kontrola_se_zapise_i_v_den_bez_prace(cfg, published, monkeypatch):
    """Den, kdy zdroj nic nevydal, po sobě nenechá manifest -- kdyby nenechal ani záznam
    o kontrole, nešel by zvenčí odlišit od dne, kdy se pipeline vůbec nespustila."""
    monkeypatch.setattr(pipeline, "window", lambda cfg, today=None: [(2026, 5), (2026, 4)])
    published[(2026, 5)] = "etag-a"
    store(cfg, 2026, 5, "etag-a")

    pipeline.check_freshness(cfg, trigger="lambda")

    checks = storage.list_names(pipeline.layout(cfg).checks_dir())
    assert len(checks) == 1
    record = storage.read_json(storage.join(pipeline.layout(cfg).checks_dir(), checks[0]))
    assert record["status"] == "ok"
    assert record["trigger"] == "lambda"
    assert record["changed"] == []  # nic se nezměnilo, a přesto se kontrolovalo
    assert record["checked"] == ["2026-05", "2026-04"]
    assert record["source_newest"] == "2026-05"


def test_kontrola_se_zapise_i_kdyz_spadne(cfg, published):
    """Že kontrola proběhla a co našla, je informace i (hlavně) u červeného běhu."""
    published[(2026, 5)] = "etag-a"

    with pytest.raises(DataQualityError):
        pipeline.check_freshness(cfg)

    checks = storage.list_names(pipeline.layout(cfg).checks_dir())
    record = storage.read_json(storage.join(pipeline.layout(cfg).checks_dir(), checks[0]))
    assert record["status"] == "failed"
    assert "bez výstupu" in record["detail"]


def test_ctenar_curated_kontrolu_nezapisuje(cfg, published, monkeypatch):
    """Build stránky se ptá na totéž co běh, ale nic nespouští -- a do bucketu nesmí psát."""
    monkeypatch.setattr(pipeline, "window", lambda cfg, today=None: [(2026, 5)])
    published[(2026, 5)] = "etag-a"
    store(cfg, 2026, 5, "etag-a")

    pipeline.check_freshness(cfg, record=False)

    assert storage.list_names(pipeline.layout(cfg).checks_dir()) == []


def test_zastaraly_zdroj_shodi_beh(cfg, published, monkeypatch):
    monkeypatch.setattr(pipeline, "window", lambda cfg, today=None: [(2020, 1)])
    published[(2020, 1)] = "etag-a"
    store(cfg, 2020, 1, "etag-a")

    with pytest.raises(DataQualityError, match="práh je 120"):
        pipeline.check_freshness(cfg)
