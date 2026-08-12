"""Jediné místo v aplikaci, které umí HTTP.

Zdroj nemá API ani manifest -- jediný způsob, jak zjistit "jsou už data?", je request na
URL. Verze souboru se pozná z ETagu: TLC soubory zpětně přepisuje (2026-03-25 přepsal
prosinec, leden i únor naráz).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx

from .errors import PermanentError, TransientError

TIMEOUT = httpx.Timeout(30.0, read=300.0)


@dataclass(frozen=True)
class SourceMeta:
    url: str
    etag: str | None
    last_modified: str | None
    bytes: int | None


def head(url: str) -> SourceMeta | None:
    """None = měsíc není publikovaný (403/404). Není to chyba běhu, jen fakt."""
    response = _request("HEAD", url)
    if response is None:
        return None
    headers = response.headers
    return SourceMeta(
        url=url,
        etag=headers.get("etag"),
        last_modified=headers.get("last-modified"),
        bytes=int(headers["content-length"]) if "content-length" in headers else None,
    )


def download(url: str) -> tuple[bytes, SourceMeta, str]:
    response = _request("GET", url)
    if response is None:
        raise PermanentError(f"{url} není publikovaný (403/404)")
    payload = response.content
    meta = SourceMeta(
        url=url,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
        bytes=len(payload),
    )
    return payload, meta, hashlib.sha256(payload).hexdigest()


def _request(method: str, url: str) -> httpx.Response | None:
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.request(method, url)
    except httpx.HTTPError as exc:  # timeout, reset, DNS -- opakování má smysl
        raise TransientError(f"{method} {url}: {exc}") from exc

    if response.status_code in (403, 404):
        return None
    if response.status_code >= 500:
        raise TransientError(f"{method} {url}: HTTP {response.status_code}")
    if response.status_code >= 400:
        raise PermanentError(f"{method} {url}: HTTP {response.status_code}")
    return response
