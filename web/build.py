"""Postaví statické stránky z curated vrstvy.

    uv run python web/build.py                                  # z ./data/curated
    APP_CURATED_URI=s3://bucket/curated uv run python web/build.py

Čte přes `storage`, takže lokální adresář i S3 jsou tentýž parametr -- stejně jako
pro pipeline samotnou. Data jsou zapečená v souboru: celá historie je jednotky MB,
takže agregát pro stránku se vejde do stovek kB a nic za běhu se nedotahuje. Žádné API,
žádná databáze, nic, co by běželo mezi zobrazeními.

Stránky jsou tři, protože otázky jsou tři. `index.html` odpovídá na "co se v New Yorku
jezdí" -- mapa, měsíce, zóny; je psaná tak, jako by byla veřejná produkční stránka, a
metodické komentáře na ní nejsou. `method.html` odpovídá na "proč jsou ta čísla taková"
-- co by udělal snadný postup, co by stál a co se dělá místo toho. `pipeline.html`
odpovídá na "dá se tomu běhu věřit" -- manifesty, prahy, co pravidla chytila.

Rozdělený je i payload, ne jen sekce: mapa je zdaleka největší kus a na provozní ani
metodické stránce nemá co dělat, manifesty zase nemá co dělat na té datové. Každá
stránka tak nese jen to, co doopravdy kreslí.

Skládá se to ze společných kusů (`style.css`, `common.js`) a dvojice per stránku
(`data.html` + `data.js`, `method.html` + `method.js`, `pipeline.html` + `pipeline.js`).
Výsledek je pořád jeden soubor na stránku -- žádný externí requestem tažený asset.

Rozsah dat ve stránce je záměrně menší než curated: denní řady, top zóny a jeden
agregát na zónu přes celou historii pro mapu -- ne všechny řádky. Kdo chce celý
výstup, čte parquet.

Obrysy zón do mapy nese `web/zones.json`, který se commituje a vyrábí ho `web/geo.py`.
Build tedy nikam nesahá pro geometrii; TLC ji mění jednou za pár let.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from datetime import date

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "src"))

from app import pipeline, storage  # noqa: E402
from app.config import Config  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
TOP_ZONES = 25

# Kostra je společná, liší se jen title, tělo a skript. Bez `<head>`: prohlížeč si ho
# doplní sám a jediné, co by v něm bylo, je `<title>` a `<style>`, které platí i takhle.
SHELL = """<!doctype html>
<html lang="cs">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<meta name="description" content="__DESC__">
<style>__STYLE__</style>
<div class="rail"></div>
__NAV__
<main>
__BODY__
</main>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>__SCRIPT__</script>
"""

# Odkazy jsou relativní bez lomítka, takže stránky fungují i otevřené z disku, a zároveň
# sedí, když Cloudflare Pages naservíruje `pipeline.html` na `/pipeline`.
NAV = (("index.html", "Data"), ("method.html", "Metodika"), ("pipeline.html", "Pipeline"))

PAGES = {
    # jméno souboru -> (zdrojová dvojice, title, popis, klíče měsíců v payloadu)
    "index.html": (
        "data",
        "Žluté taxíky NYC po zónách",
        "Jízdy žlutých medailonových taxíků v New Yorku podle zóny nástupu a podle dne.",
        ("key", "year", "month", "trips", "revenue", "daily", "zones", "zones_total"),
    ),
    # Metodická stránka počítá z manifestů totéž co provozní, jen přes celou historii
    # místo jednoho běhu -- proto tytéž klíče a žádná mapa ani denní řady.
    "method.html": (
        "method",
        "Kvalita dat NYC Taxi: polemika",
        "Proč zveřejněná čísla vypadají, jak vypadají: co by stála snadná odpověď, které"
        " prahy se změřily a co výstup pořád ještě neumí.",
        ("key", "year", "month", "rows", "trips", "runs"),
    ),
    "pipeline.html": (
        "pipeline",
        "Pipeline a kvalita dat NYC Taxi",
        "Jak dataset newyorských taxíků vzniká: běhy, manifesty, pravidla kvality a prahy.",
        ("key", "year", "month", "rows", "trips", "runs"),
    ),
}

# Prahy a okna do sekce "The pipeline". Berou se ze stejné konfigurace jako pipeline,
# aby stránka nemohla tvrdit jiná čísla, než podle kterých se běh doopravdy řídí.
CONFIG_KEYS = (
    "lookback_months",
    "source_stale_days",
    "max_speed_mph",
    "max_distance_mi",
    "max_duration_min",
    "max_reject_ratio",
    "max_volume_delta",
    "max_null_ratio_distance",
    "max_null_ratio_duration",
)


def partitions(layout: storage.Layout) -> list[tuple[int, int]]:
    """Které partition v curated existují. Sidecar `_runs` je přeskočený -- partition
    je adresář s parquetem, ne s manifesty."""
    base = storage.join(layout.curated_uri, "dataset=yellow")
    found = []
    for year_dir in storage.list_prefixes(base):
        if not year_dir.startswith("year="):
            continue
        year = int(year_dir.removeprefix("year="))
        for month_dir in storage.list_prefixes(storage.join(base, year_dir)):
            if month_dir.startswith("month="):
                found.append((year, int(month_dir.removeprefix("month="))))
    return sorted(found)


def read_runs(layout: storage.Layout, year: int, month: int) -> list[dict]:
    """Manifesty partition, nejnovější první. Append-only: partition se přepisuje,
    manifesty přibývají."""
    runs_dir = layout.runs_dir(year, month)
    runs = [
        storage.read_json(storage.join(runs_dir, name))
        for name in storage.list_names(runs_dir)
        if name.endswith(".json")
    ]
    return sorted(runs, key=lambda r: r["run_id"], reverse=True)


def zone_sums(frame: pl.DataFrame) -> pl.DataFrame:
    """Součty na zónu. Průměr vzdálenosti i jízdného nese svůj jmenovatel zvlášť, aby
    se daly sečíst přes víc měsíců a vážený průměr dopočítat až nakonec; medián takhle
    sečíst nejde, bere se medián denních mediánů a stránka ho tak i popisuje."""
    return frame.group_by("location_id", "borough", "zone").agg(
        pl.col("trips").sum().alias("trips"),
        pl.col("yellow_revenue_usd").sum().round(0).alias("revenue"),
        pl.col("distance_obs").sum().alias("distance_obs"),
        (pl.col("avg_distance_mi") * pl.col("distance_obs")).sum().alias("_dist"),
        pl.col("median_distance_mi").median().round(2).alias("median_distance_mi"),
        pl.col("duration_obs").sum().alias("duration_obs"),
        (pl.col("avg_duration_min") * pl.col("duration_obs")).sum().alias("_dur"),
        (pl.col("avg_fare_usd") * pl.col("fare_obs")).sum().alias("_fare"),
        pl.col("fare_obs").sum().alias("fare_obs"),
    )


def zone_stats(sums: pl.DataFrame) -> pl.DataFrame:
    """Ze součtů udělá čísla k zobrazení, seřazená od nejvytíženější zóny."""
    # Dělit se smí, jen když je čím. Zóna s hrstkou jízd může mít nula pozorování
    # vzdálenosti nebo jízdného a 0/0 je v polars NaN -- což `json.dumps` zapíše jako
    # literál NaN a `JSON.parse` na něm spadne, takže by se rozsypala celá stránka,
    # ne jen jedna buňka. Prázdný jmenovatel je null: "není z čeho počítat".
    return (
        sums.with_columns(
            avg_distance_mi=pl.when(pl.col("distance_obs") > 0)
            .then(pl.col("_dist") / pl.col("distance_obs"))
            .round(2),
            avg_duration_min=pl.when(pl.col("duration_obs") > 0)
            .then(pl.col("_dur") / pl.col("duration_obs"))
            .round(2),
            avg_fare_usd=pl.when(pl.col("fare_obs") > 0)
            .then(pl.col("_fare") / pl.col("fare_obs"))
            .round(2),
            # `coverage` je jmenovatel vzdálenosti -- ten nese tabulka mean/median níž na
            # stránce. Doba jízdy má svůj vlastní a liší se: na mapě se dá obarvit rychlost,
            # která stojí na obou, takže musí být vidět oba.
            coverage=pl.when(pl.col("trips") > 0)
            .then(pl.col("distance_obs") / pl.col("trips"))
            .round(4),
            duration_coverage=pl.when(pl.col("trips") > 0)
            .then(pl.col("duration_obs") / pl.col("trips"))
            .round(4),
        )
        .drop("_dist", "_dur", "_fare")
        .sort("trips", descending=True)
    )


def merge_zone_sums(monthly: list[pl.DataFrame]) -> pl.DataFrame:
    """Součty za celou historii. Sečte se totéž co v měsíci -- vážené čitatele, ne už
    hotové průměry -- takže agregát přes historii je stejné číslo jako přes jeden měsíc."""
    return (
        pl.concat(monthly)
        .group_by("location_id", "borough", "zone")
        .agg(
            pl.col("trips").sum(),
            pl.col("revenue").sum(),
            pl.col("distance_obs").sum(),
            pl.col("_dist").sum(),
            pl.col("median_distance_mi").median().round(2),
            pl.col("duration_obs").sum(),
            pl.col("_dur").sum(),
            pl.col("_fare").sum(),
            pl.col("fare_obs").sum(),
        )
    )


def month_payload(layout: storage.Layout, year: int, month: int) -> tuple[dict, pl.DataFrame]:
    frame = storage.scan_parquet(layout.curated_file(year, month)).collect()

    daily = (
        frame.group_by("date")
        .agg(
            pl.col("trips").sum().alias("trips"),
            pl.col("yellow_revenue_usd").sum().round(2).alias("revenue"),
            pl.col("refunds_usd").sum().round(2).alias("refunds"),
        )
        .sort("date")
    )

    sums = zone_sums(frame)
    zones = zone_stats(sums)

    payload = {
        "key": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "rows": frame.height,
        "trips": int(frame["trips"].sum()),
        "revenue": round(float(frame["yellow_revenue_usd"].sum()), 2),
        "refunds": round(float(frame["refunds_usd"].sum()), 2),
        "runs": read_runs(layout, year, month),
        "daily": daily.to_dicts(),
        "zones": zones.head(TOP_ZONES).to_dicts(),
        "zones_total": zones.height,
    }
    return payload, sums


def map_payload(monthly: list[pl.DataFrame]) -> dict:
    """Podklad pro choropleth: obrysy zón a k nim čísla za celou historii.

    Mapa vědomě nesleduje měsíční přepínač zbytku stránky -- 265 zón krát každý měsíc
    by payload nafouklo řádově víc než celá dosavadní stránka. Jeden agregát přes
    historii ukáže totéž rozložení a stránka u mapy říká, za jaké období je.
    """
    geo = json.loads((HERE / "zones.json").read_text())
    zones = zone_stats(merge_zone_sums(monthly)).select(
        "location_id",
        "borough",
        "zone",
        "trips",
        "revenue",
        "avg_fare_usd",
        "avg_distance_mi",
        "avg_duration_min",
        "coverage",
        "duration_coverage",
    )

    # Zóny 264/265 ("NV", "Outside of NYC") žádný obrys nemají a mít nebudou: nejsou to
    # místa, ale zbytkové kódy. Jejich jízdy se do mapy nevejdou, tak ať to stránka řekne.
    drawn = set(geo["zones"])
    off_map = zones.filter(~pl.col("location_id").cast(pl.String).is_in(drawn))

    return {
        "width": geo["width"],
        "height": geo["height"],
        "paths": geo["zones"],
        "names": geo["names"],
        "zones": zones.to_dicts(),
        "off_map": {
            "zones": off_map.height,
            "trips": int(off_map["trips"].sum()),
        },
    }


def nav(active: str) -> str:
    tabs = ""
    for href, text in NAV:
        # `aria-current`, ne jen barva: která ze dvou stránek je otevřená, musí být
        # k dispozici i odečítači obrazovky, ne jen oku.
        on = ' aria-current="page"' if href == active else ""
        tabs += f'<a class="tab" href="{href}"{on}>{text}</a>'
    return (
        '<nav class="topnav"><div class="wrap"><span class="brand">nyc-taxi-etl</span>'
        f'<div class="tabs">{tabs}</div></div></nav>'
    )


def encode(payload: dict) -> str:
    """Payload do `<script type="application/json">`.

    `allow_nan=False`: NaN ani Infinity v JSONu neexistují, prohlížeč by na payloadu
    spadl a stránka by zůstala prázdná. Ať to radši padne tady.
    """
    encoded = json.dumps(payload, separators=(",", ":"), default=str, allow_nan=False)
    if "</script" in encoded:
        raise SystemExit("payload obsahuje </script, stránka by se rozpadla")
    return encoded


def render(filename: str, name: str, title: str, desc: str, payload: dict, style: str) -> str:
    """Poskládá stránku. `__PAYLOAD__` se dosazuje jako poslední -- v datech může být
    cokoli, včetně řetězce, který vypadá jako další placeholder."""
    page = (
        SHELL.replace("__TITLE__", title)
        .replace("__DESC__", desc)
        .replace("__STYLE__", style)
        .replace("__NAV__", nav(filename))
        .replace("__BODY__", (HERE / f"{name}.html").read_text())
        .replace("__SCRIPT__", (HERE / "common.js").read_text() + (HERE / f"{name}.js").read_text())
    )
    return page.replace("__PAYLOAD__", encode(payload))


def build() -> list[pathlib.Path]:
    cfg = Config.load()
    layout = pipeline.layout(cfg)

    built = [month_payload(layout, year, month) for year, month in partitions(layout)]
    if not built:
        raise SystemExit(f"v {layout.curated_uri} nejsou žádné partition")
    months = [payload for payload, _ in built]

    # Co je na obou stránkách: odkud data jsou, kdy se to postavilo, jak čerstvý je zdroj.
    # Bez URI curated -- jméno bucketu nese číslo AWS účtu a stránky jsou veřejné.
    common = {
        "dataset": "yellow",
        "generated_at": date.today().isoformat(),
        "source": {
            "store": "S3" if layout.curated_uri.startswith("s3://") else "disk",
            "region": os.environ.get("AWS_REGION", "eu-central-1"),
        },
        "config": {key: getattr(cfg, key) for key in CONFIG_KEYS},
        "freshness": pipeline.check_freshness(cfg),
    }

    style = (HERE / "style.css").read_text()
    out_dir = HERE / "dist"
    out_dir.mkdir(exist_ok=True)

    written = []
    for filename, (name, title, desc, keys) in PAGES.items():
        payload = common | {"months": [{k: m[k] for k in keys} for m in months]}
        if name == "data":
            payload["map"] = map_payload([sums for _, sums in built])

        out = out_dir / filename
        out.write_text(render(filename, name, title, desc, payload, style))
        written.append(out)
        print(f"{out} · {out.stat().st_size / 1024:.0f} kB")

    print(
        f"{len(months)} partition · {sum(len(m['runs']) for m in months)} manifestů"
        f" · {months[0]['key']} → {months[-1]['key']}"
    )
    return written


if __name__ == "__main__":
    build()
