"""Cesty jsou URI, ne Path: `./data/curated` i `s3://bucket/curated` je tentýž parametr.

Jedna partition = jeden deterministicky pojmenovaný soubor. Přepis je pak atomický na
obou platformách (lokálně `os.replace`, na S3 jedno PUT) a nikdy nemůže vzniknout
partition se soubory ze dvou verzí zdroje.
"""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import polars as pl

RAW_FILE = "yellow_tripdata_{year:04d}-{month:02d}.parquet"
CURATED_FILE = "yellow_taxi_trips_by_zone.parquet"
REJECTS_FILE = "rejects.parquet"
META_FILE = "_meta.json"


def join(*parts: str) -> str:
    return "/".join(part.rstrip("/") for part in parts if part)


def _s3(uri: str):
    if not uri.startswith("s3://"):
        return None
    import boto3  # jen na Lambdě; lokálně se neimportuje

    parsed = urlparse(uri)
    return boto3.client("s3"), parsed.netloc, parsed.path.lstrip("/")


NOT_FOUND = {"404", "NoSuchKey", "NotFound"}


def exists(uri: str) -> bool:
    target = _s3(uri)
    if target is None:
        return Path(uri).exists()
    client, bucket, key = target
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.ClientError as error:
        # Jen "není tam" znamená False. AccessDenied nebo 5xx se musí propsat ven:
        # jinak vypadají jako chybějící soubor a pipeline mlčky stáhne a přepíše data.
        if error.response["Error"]["Code"] in NOT_FOUND:
            return False
        raise


def read_bytes(uri: str) -> bytes:
    target = _s3(uri)
    if target is None:
        return Path(uri).read_bytes()
    client, bucket, key = target
    return client.get_object(Bucket=bucket, Key=key)["Body"].read()


def write_bytes(uri: str, payload: bytes) -> None:
    target = _s3(uri)
    if target is None:
        path = Path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, path)  # atomický přepis
        return
    client, bucket, key = target
    client.put_object(Bucket=bucket, Key=key, Body=payload)


def read_json(uri: str) -> dict:
    return json.loads(read_bytes(uri))


def write_json(uri: str, document: dict) -> None:
    write_bytes(uri, json.dumps(document, indent=2, default=str).encode())


def write_parquet(uri: str, frame: pl.DataFrame) -> None:
    buffer = io.BytesIO()
    frame.write_parquet(buffer, compression="zstd")
    write_bytes(uri, buffer.getvalue())


def list_names(uri: str) -> list[str]:
    """Stránkuje: `_runs/` je append-only a jedna odpověď `list_objects_v2` vrací max
    1000 klíčů. Bez paginátoru by po tisícím manifestu `names[-1]` přestal být poslední."""
    target = _s3(uri)
    if target is None:
        directory = Path(uri)
        return sorted(item.name for item in directory.iterdir()) if directory.is_dir() else []
    client, bucket, prefix = target
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix.rstrip("/") + "/"
    )
    return sorted(
        item["Key"].rsplit("/", 1)[-1] for page in pages for item in page.get("Contents", [])
    )


def list_prefixes(uri: str) -> list[str]:
    """Názvy „adresářů" o úroveň níž, bez koncového lomítka. Na S3 to `list_names`
    neumí: bez `Delimiter` je výpis rekurzivní a `rsplit("/")` z něj vrátí jména
    souborů, ne partition. Používá se při procházení `dataset=/year=/month=`."""
    target = _s3(uri)
    if target is None:
        directory = Path(uri)
        return sorted(item.name for item in directory.iterdir() if item.is_dir())
    client, bucket, prefix = target
    pages = client.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=prefix.rstrip("/") + "/", Delimiter="/"
    )
    return sorted(
        item["Prefix"].rstrip("/").rsplit("/", 1)[-1]
        for page in pages
        for item in page.get("CommonPrefixes", [])
    )


def scan_parquet(uri: str) -> pl.LazyFrame:
    """Lazy scan přes obě úložiště. Na S3 se soubor natáhne do paměti a scanuje se buffer:
    projection pushdown zůstává (naměřeno 467 -> 146 MB, `PROJECT 6/20 COLUMNS`), jen se
    přenese celý objekt. Nativní `pl.scan_parquet("s3://...")` by stáhl jen potřebné
    sloupce, ale bere credentials vlastní cestou (object_store) mimo boto3 -- druhý
    autentizační kanál kvůli 56 MB stojí za víc, než ušetří."""
    target = _s3(uri)
    if target is None:
        return pl.scan_parquet(uri)
    return pl.scan_parquet(io.BytesIO(read_bytes(uri)))


@dataclass(frozen=True)
class Layout:
    """Rozvržení vrstev. `dataset=yellow` je místo, kam by případný další dataset TLC
    přibyl bez přepisu cest."""

    raw_uri: str
    curated_uri: str
    rejects_uri: str

    def _partition(self, base: str, year: int, month: int) -> str:
        return join(base, "dataset=yellow", f"year={year:04d}", f"month={month:02d}")

    def raw_file(self, year: int, month: int) -> str:
        return join(
            self._partition(self.raw_uri, year, month), RAW_FILE.format(year=year, month=month)
        )

    def raw_meta(self, year: int, month: int) -> str:
        return join(self._partition(self.raw_uri, year, month), META_FILE)

    def curated_file(self, year: int, month: int) -> str:
        return join(self._partition(self.curated_uri, year, month), CURATED_FILE)

    def rejects_file(self, year: int, month: int) -> str:
        return join(self._partition(self.rejects_uri, year, month), REJECTS_FILE)

    def runs_dir(self, year: int, month: int) -> str:
        return join(self._partition(self.curated_uri, year, month), "_runs")

    def run_file(self, year: int, month: int, run_id: str) -> str:
        return join(self.runs_dir(year, month), f"{run_id}.json")
