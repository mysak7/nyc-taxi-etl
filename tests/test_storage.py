"""Jedna partition = jeden soubor, přepis je atomický. Testuje se na úrovni zápisu, ne
transformace -- idempotence je vlastnost úložiště."""

from pathlib import Path

import polars as pl

from app import storage


def frame(values: list[int]) -> pl.DataFrame:
    return pl.DataFrame({"location_id": values, "trips": [1] * len(values)})


def test_dvakrat_zapsany_vystup_je_bajtove_stejny(tmp_path):
    uri = storage.Layout(str(tmp_path), str(tmp_path), str(tmp_path)).curated_file(2025, 1)

    storage.write_parquet(uri, frame([1, 2, 3]))
    first = Path(uri).read_bytes()
    storage.write_parquet(uri, frame([1, 2, 3]))

    assert Path(uri).read_bytes() == first


def test_prepis_partition_nezanecha_stary_soubor_ani_tmp(tmp_path):
    layout = storage.Layout(str(tmp_path), str(tmp_path), str(tmp_path))
    uri = layout.curated_file(2025, 1)

    storage.write_parquet(uri, frame([1, 2, 3]))
    storage.write_parquet(uri, frame([9]))

    partition = Path(uri).parent
    assert [item.name for item in partition.iterdir()] == [storage.CURATED_FILE]
    assert pl.read_parquet(uri)["location_id"].to_list() == [9]


def test_manifesty_se_neprepisuji_ale_pribyvaji(tmp_path):
    layout = storage.Layout(str(tmp_path), str(tmp_path), str(tmp_path))

    storage.write_json(layout.run_file(2025, 1, "2026-08-12T09:00:00Z-aaa"), {"rows": {"input": 1}})
    storage.write_json(layout.run_file(2025, 1, "2026-08-12T10:00:00Z-bbb"), {"rows": {"input": 2}})

    names = storage.list_names(layout.runs_dir(2025, 1))
    assert len(names) == 2
    assert names[-1].startswith("2026-08-12T10")  # ls řadí chronologicky, poslední je nejnovější


def test_prazdny_adresar_manifestu_neni_chyba(tmp_path):
    layout = storage.Layout(str(tmp_path), str(tmp_path), str(tmp_path))

    assert storage.list_names(layout.runs_dir(2025, 1)) == []
