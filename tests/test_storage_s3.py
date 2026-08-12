"""S3 větev `storage` -- ta, která se jinak poprvé spustí až na Lambdě.

Fake místo moto: potřebné API jsou čtyři volání a `boto3` je volitelná závislost
(`--extra lambda`), takže testy ho nesmí vyžadovat. Že `client.exceptions.ClientError`
funguje i tady, je záměr: výjimka patří ke klientovi, ne k importu z botocore.
"""

from __future__ import annotations

import io
from urllib.parse import urlparse

import polars as pl
import pytest

from app import storage

BUCKET = "s3://nyc-taxi-etl/curated"


class ClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """In-memory S3. `denied` simuluje bucket policy, která nás k prefixu nepustí."""

    class exceptions:
        ClientError = ClientError

    def __init__(self, denied: str | None = None):
        self.objects: dict[str, bytes] = {}
        self.denied = denied

    def _guard(self, key: str) -> None:
        if self.denied and key.startswith(self.denied):
            raise ClientError("AccessDenied")

    def head_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803 -- podpis boto3
        self._guard(Key)
        if Key not in self.objects:
            raise ClientError("404")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket: str, Key: str) -> dict:  # noqa: N803
        self._guard(Key)
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:  # noqa: N803
        self._guard(Key)
        self.objects[Key] = Body

    def get_paginator(self, operation: str) -> FakeS3:
        assert operation == "list_objects_v2"
        return self

    def paginate(self, Bucket: str, Prefix: str):  # noqa: N803
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        for start in range(0, len(keys), 1000):  # skutečné S3 vrací max 1000 klíčů na stránku
            yield {"Contents": [{"Key": key} for key in keys[start : start + 1000]]}


@pytest.fixture
def s3(monkeypatch) -> FakeS3:
    fake = FakeS3()

    def route(uri: str):
        if not uri.startswith("s3://"):
            return None
        parsed = urlparse(uri)
        return fake, parsed.netloc, parsed.path.lstrip("/")

    monkeypatch.setattr(storage, "_s3", route)
    return fake


def test_parquet_se_na_s3_scanuje_lazy_a_cte_jen_potrebne_sloupce(s3):
    uri = storage.join(BUCKET, "part.parquet")
    storage.write_parquet(uri, pl.DataFrame({"a": [1, 2], "b": ["x", "y"], "c": [1.5, 2.5]}))

    scan = storage.scan_parquet(uri)

    assert scan.select("a").collect()["a"].to_list() == [1, 2]
    # Kvůli tomuhle řádku to je scan a ne read: 20 sloupců zdroje = 467 MB, 6 = 146 MB.
    assert "PROJECT 1/3 COLUMNS" in scan.select("a").explain()


def test_seznam_manifestu_strankuje_pres_hranici_1000_klicu(s3):
    layout = storage.Layout(BUCKET, BUCKET, BUCKET)
    for index in range(1500):
        storage.write_json(layout.run_file(2025, 1, f"2026-08-12T{index:04d}"), {"i": index})

    names = storage.list_names(layout.runs_dir(2025, 1))

    assert len(names) == 1500
    assert names[-1] == "2026-08-12T1499.json"  # bez paginátoru by tu bylo 0999


def test_chybejici_objekt_je_false_ale_access_denied_se_neschova(monkeypatch):
    fake = FakeS3(denied="secret/")
    monkeypatch.setattr(storage, "_s3", lambda uri: (fake, "b", urlparse(uri).path.lstrip("/")))

    assert storage.exists("s3://b/curated/chybi.parquet") is False
    # Kdyby AccessDenied vracelo False, pipeline by ho četla jako "výstup neexistuje"
    # a mlčky přepsala data, ke kterým nemá právo číst.
    with pytest.raises(ClientError):
        storage.exists("s3://b/secret/vystup.parquet")
