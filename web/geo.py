"""Připraví obrysy taxi zón pro stránku: `uv run python web/geo.py`.

Stahuje oficiální shapefile TLC a vyrábí z něj `web/zones.json` -- hotové SVG cesty
klíčované LocationID. Výstup se commituje, takže `build.py` ani CI nikam nesahají;
tenhle skript se pouští ručně, když TLC vydá novou verzi zón (děje se po letech).

Bez geopandas a bez shapely: shapefile je pár structů a Douglas-Peucker je dvacet
řádků. Přidávat kvůli jednomu offline kroku dvousetmegovou závislost do pipeline,
která jinak jede na polars, se nevyplatí.

Souřadnice se nepřevádějí na WGS84. Shapefile je v EPSG:2263 (State Plane New York
Long Island, stopy) -- to je konformní projekce v rovině, tedy přesně to, co SVG chce.
Stačí posunout počátek a otočit osu Y; převod na stupně a zpět by tvar jen zhoršil.
"""

from __future__ import annotations

import io
import json
import math
import pathlib
import struct
import urllib.request
import zipfile

SOURCE = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip"
HERE = pathlib.Path(__file__).resolve().parent

# Šířka výstupního viewBoxu. Mapa se v stránce kreslí na ~600 px, takže jedna jednotka
# je zhruba půl pixelu a zjednodušení pod ni není okem vidět.
WIDTH = 1000.0
TOLERANCE = 0.6  # Douglas-Peucker, v jednotkách viewBoxu
MIN_RING_AREA = 0.35  # menší ostrůvek než tohle (jednotky²) je na mapě míň než tečka


def read_dbf(blob: bytes) -> list[dict[str, str]]:
    """Jen tolik dBASE III, kolik má shapefile atributů: hlavička, popisy sloupců,
    pak pevně široké záznamy."""
    count, header_len, record_len = struct.unpack_from("<IHH", blob, 4)
    fields = []
    offset = 32
    while blob[offset] != 0x0D:
        # 11 bajtů jméno, 1 typ, 4 nepoužité (adresa v paměti), pak teprve šířka.
        name, _, width = struct.unpack_from("<11sc4xB", blob, offset)
        fields.append((name.rstrip(b"\0").decode(), width))
        offset += 32

    rows = []
    for i in range(count):
        cursor = header_len + i * record_len + 1  # +1 = příznak smazaného záznamu
        row = {}
        for name, width in fields:
            row[name] = blob[cursor : cursor + width].decode("latin-1").strip()
            cursor += width
        rows.append(row)
    return rows


def read_shp(blob: bytes) -> list[list[list[tuple[float, float]]]]:
    """Polygony ze .shp v pořadí záznamů -- tedy v pořadí řádků .dbf. Každý tvar je
    seznam prstenců; díry jsou prstence s opačnou orientací a řeší je fill-rule."""
    shapes = []
    offset = 100  # hlavička souboru
    while offset < len(blob):
        (content_words,) = struct.unpack_from(">I", blob, offset + 4)
        body = offset + 8
        (shape_type,) = struct.unpack_from("<I", blob, body)
        if shape_type == 0:  # null shape: zóna bez geometrie
            shapes.append([])
            offset = body + content_words * 2
            continue
        if shape_type != 5:
            raise SystemExit(f"čekal jsem polygon (5), přišlo {shape_type}")

        n_parts, n_points = struct.unpack_from("<II", blob, body + 36)
        parts = struct.unpack_from(f"<{n_parts}I", blob, body + 44)
        coords = struct.unpack_from(f"<{2 * n_points}d", blob, body + 44 + n_parts * 4)

        bounds = [*parts, n_points]
        rings = [
            [(coords[2 * i], coords[2 * i + 1]) for i in range(bounds[p], bounds[p + 1])]
            for p in range(n_parts)
        ]
        shapes.append(rings)
        offset = body + content_words * 2
    return shapes


def simplify(ring: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    """Douglas-Peucker, iterativně -- rekurze by na prstenci o deseti tisících bodech
    narazila na limit zásobníku."""
    if len(ring) < 4:
        return ring

    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        ax, ay = ring[first]
        bx, by = ring[last]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        worst, at = -1.0, first
        for i in range(first + 1, last):
            px, py = ring[i]
            # Degenerovaná tětiva (uzavřený prstenec, A == B): měř vzdálenost od bodu.
            if norm == 0:
                dist = math.hypot(px - ax, py - ay)
            else:
                dist = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if dist > worst:
                worst, at = dist, i
        if worst > tol:
            keep[at] = True
            stack.append((first, at))
            stack.append((at, last))

    return [p for p, k in zip(ring, keep, strict=True) if k]


def area(ring: list[tuple[float, float]]) -> float:
    """Plocha přes shoelace, v absolutní hodnotě -- tady jde jen o velikost, ne o to,
    jestli je prstenec dírou."""
    total = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2


def path_of(rings: list[list[tuple[float, float]]]) -> str:
    parts = []
    for ring in rings:
        head, *tail = ring
        moves = " ".join(f"{x:.1f},{y:.1f}" for x, y in tail)
        parts.append(f"M{head[0]:.1f},{head[1]:.1f}L{moves}Z")
    return "".join(parts)


def build() -> pathlib.Path:
    print(f"stahuji {SOURCE}")
    with urllib.request.urlopen(SOURCE, timeout=120) as response:  # noqa: S310
        archive = zipfile.ZipFile(io.BytesIO(response.read()))

    attributes = read_dbf(archive.read("taxi_zones/taxi_zones.dbf"))
    shapes = read_shp(archive.read("taxi_zones/taxi_zones.shp"))
    if len(attributes) != len(shapes):
        raise SystemExit(f"{len(attributes)} atributů, ale {len(shapes)} tvarů")

    # Měřítko z celkového rozsahu všech zón, ne z bboxu v hlavičce: ten počítá i se
    # záznamy, které nakonec zahodíme.
    points = [p for rings in shapes for ring in rings for p in ring]
    min_x = min(x for x, _ in points)
    max_x = max(x for x, _ in points)
    min_y = min(y for _, y in points)
    max_y = max(y for _, y in points)
    scale = WIDTH / (max_x - min_x)
    height = (max_y - min_y) * scale

    zones: dict[str, str] = {}
    names: dict[str, list[str]] = {}
    kept_points = raw_points = 0
    for row, rings in zip(attributes, shapes, strict=True):
        planar = [
            # Y se otáčí: shapefile roste na sever, SVG dolů.
            [((x - min_x) * scale, (max_y - y) * scale) for x, y in ring]
            for ring in rings
        ]
        raw_points += sum(len(ring) for ring in planar)

        thinned = [simplify(ring, TOLERANCE) for ring in planar]
        thinned = [ring for ring in thinned if len(ring) >= 3]
        # Ostrůvky pod prahem pryč, ale zóna, která je celá jedním ostrůvkem
        # (Governors Island, Roosevelt Island), si svůj největší prstenec nechá.
        if thinned:
            biggest = max(area(ring) for ring in thinned)
            thinned = [
                ring for ring in thinned if area(ring) >= MIN_RING_AREA or area(ring) == biggest
            ]
        if not thinned:
            continue

        kept_points += sum(len(ring) for ring in thinned)
        zones[row["LocationID"]] = path_of(thinned)
        # Popisek pro zóny, které v curated nemají ani řádek: mapa je pořád kreslí, tak
        # ať se dá najet i na ně. Kde curated data má, vyhrává jméno z lookupu, po kterém
        # jede pipeline.
        names[row["LocationID"]] = [row["zone"], row["borough"]]

    out = HERE / "zones.json"
    out.write_text(
        json.dumps(
            {
                "width": round(WIDTH),
                "height": round(height),
                "zones": zones,
                "names": names,
            },
            separators=(",", ":"),
        )
    )
    missing = sorted(int(r["LocationID"]) for r in attributes if r["LocationID"] not in zones)
    print(
        f"{out} · {out.stat().st_size / 1024:.0f} kB · {len(zones)} zón"
        f" · {raw_points} → {kept_points} bodů"
        + (f" · bez geometrie: {missing}" if missing else "")
    )
    return out


if __name__ == "__main__":
    build()
