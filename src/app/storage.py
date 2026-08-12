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


def exists(uri: str) -> bool:
    target = _s3(uri)
    if target is None:
        return Path(uri).exists()
    client, bucket, key = target
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


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
    target = _s3(uri)
    if target is None:
        directory = Path(uri)
        return sorted(item.name for item in directory.iterdir()) if directory.is_dir() else []
    client, bucket, prefix = target
    response = client.list_objects_v2(Bucket=bucket, Prefix=prefix.rstrip("/") + "/")
    return sorted(item["Key"].rsplit("/", 1)[-1] for item in response.get("Contents", []))


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
