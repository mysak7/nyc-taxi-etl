"""Postaví statické stránky z curated vrstvy.

    uv run python web/build.py                                  # z ./data/curated
    APP_CURATED_URI=s3://bucket/curated uv run python web/build.py

Čte přes `storage`, takže lokální adresář i S3 jsou tentýž parametr -- stejně jako
pro pipeline samotnou. Data jsou zapečená v souboru: celá historie je jednotky MB,
takže agregát pro stránku se vejde do stovek kB a nic za běhu se nedotahuje. Žádné API,
žádná databáze, nic, co by běželo mezi zobrazeními.

Stránky jsou čtyři, protože otázky jsou čtyři. `index.html` odpovídá na "co se v New Yorku
jezdí" -- mapa, měsíce, zóny; je psaná tak, jako by byla veřejná produkční stránka, a
metodické komentáře na ní nejsou. `method.html` odpovídá na "proč jsou ta čísla taková"
-- co by udělal snadný postup, co by stál a co se dělá místo toho. `pipeline.html`
odpovídá na "dá se tomu běhu věřit" -- manifesty, prahy, co pravidla chytila.
`quarantine.html` odpovídá na "co v těch číslech není" -- karanténa rozepsaná po měsících,
důvod po důvodu.

Rozdělený je i payload, ne jen sekce: mapa je zdaleka největší kus a na provozní ani
metodické stránce nemá co dělat, manifesty zase nemá co dělat na té datové. Každá
stránka tak nese jen to, co doopravdy kreslí.

Skládá se to ze společných kusů (`style.css`, `common.js`) a dvojice per stránku
(`data.html` + `data.js`, `method.html` + `method.js`, `pipeline.html` + `pipeline.js`,
`quarantine.html` + `quarantine.js`).
Výsledek je pořád jeden soubor na stránku -- žádný externí requestem tažený asset.

Rozsah dat ve stránce je záměrně menší než curated: denní řady a jeden součet na
(zónu, měsíc) -- ne všechny řádky. Kdo chce celý výstup, čte parquet.

Datová stránka je jeden řízený pohled: čtenář si vybere měsíc a metriku a všechno, co
vidí, je z toho měsíce a v té metrice. Payload tomu odpovídá -- nese sčitatelné součty,
ne hotové průměry, aby si stránka mohla průměr dopočítat pro libovolný řez, na který se
někdo zeptá.

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

# Sloupce curated, bez kterých se stránka nepostaví. Není to celý výstup pipeline: co
# stránka nekreslí, ať klidně chybí.
REQUIRED_COLUMNS = (
    "date",
    "location_id",
    "borough",
    "zone",
    "trips",
    "yellow_revenue_usd",
    "refunds_usd",
    "net_revenue_usd",
    "avg_distance_mi",
    "median_distance_mi",
    "distance_obs",
    "avg_duration_min",
    "median_duration_min",
    "duration_obs",
    "avg_fare_usd",
    "fare_obs",
)

# Co se do stránky posílá o každém řezu dat (zóna v měsíci, jeden den). Všechno jsou
# *sčitatelné* veličiny -- součty a jejich jmenovatele, ne hotové průměry. Průměr si
# stránka dopočítá pro libovolný řez, který si čtenář vybere: jedna zóna v jednom
# měsíci, celý měsíc, celá historie. Kdyby se posílal průměr, šlo by ho zpátky sečíst
# jen s vahami, které by v payloadu stejně musely být -- takhle je v něm rovnou to,
# co je additivní, a průměr vzniká až na poslední chvíli a všude stejným vzorcem.
SUM_COLUMNS = (
    "trips",
    "revenue",
    "refunds",
    "dist_obs",
    "dist_sum",
    "dur_obs",
    "dur_sum",
    "fare_obs",
    "fare_sum",
)

# Medián sečíst nejde, takže stojí mimo `SUM_COLUMNS`: bere se medián denních mediánů
# a stránka ho tak i popisuje. Je jen u zón, ne u dnů -- tabulka průměr/medián je řez
# přes zóny jednoho měsíce.
MEDIAN_COLUMNS = ("median_dist", "median_dur")

# Vážené součty se počítají zpátky z průměru a jeho jmenovatele, protože curated nese
# průměr (to je číslo, které se vykazuje) a ne součet. Zaokrouhlení je na jednotku,
# ve které se pak čte: dolary celé, míle a minuty na desetinu.
SUM_AGGREGATES = (
    pl.col("trips").sum().alias("trips"),
    pl.col("yellow_revenue_usd").sum().round(0).cast(pl.Int64).alias("revenue"),
    pl.col("refunds_usd").sum().round(0).cast(pl.Int64).alias("refunds"),
    pl.col("distance_obs").sum().alias("dist_obs"),
    (pl.col("avg_distance_mi") * pl.col("distance_obs")).sum().round(1).alias("dist_sum"),
    pl.col("duration_obs").sum().alias("dur_obs"),
    (pl.col("avg_duration_min") * pl.col("duration_obs")).sum().round(1).alias("dur_sum"),
    pl.col("fare_obs").sum().alias("fare_obs"),
    (pl.col("avg_fare_usd") * pl.col("fare_obs")).sum().round(0).cast(pl.Int64).alias("fare_sum"),
)

MEDIAN_AGGREGATES = (
    pl.col("median_distance_mi").median().round(2).alias("median_dist"),
    pl.col("median_duration_min").median().round(2).alias("median_dur"),
)

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
NAV = (
    ("index.html", "Data"),
    ("method.html", "Metodika"),
    ("pipeline.html", "Pipeline"),
    ("quarantine.html", "Karanténa"),
)

PAGES = {
    # jméno souboru -> (zdrojová dvojice, title, popis, klíče měsíců v payloadu)
    "index.html": (
        "data",
        "Žluté taxíky NYC po zónách",
        "Jízdy žlutých medailonových taxíků v New Yorku podle zóny nástupu a podle dne.",
        # Bez měsíčních součtů: ty se dají sečíst ze `zones` a jeden zdroj čísla znamená,
        # že se dlaždice nemůže rozejít s mapou pod ní.
        ("key", "year", "month", "daily", "zones"),
    ),
    # Metodická stránka počítá z manifestů totéž co provozní, jen přes celou historii
    # místo jednoho běhu -- proto tytéž klíče a žádná mapa ani denní řady.
    "method.html": (
        "method",
        "Metodika a kvalita dat NYC Taxi",
        "Rozhodnutí za zveřejněnými čísly: co by stála jednodušší varianta, jak se"
        " změřily prahy a jaká omezení výstup nese.",
        ("key", "year", "month", "rows", "trips", "runs"),
    ),
    "pipeline.html": (
        "pipeline",
        "Pipeline a kvalita dat NYC Taxi",
        "Jak dataset newyorských taxíků vzniká: běhy, manifesty, pravidla kvality a prahy.",
        ("key", "year", "month", "rows", "trips", "runs"),
    ),
    # Karanténa je řada, ne jeden běh: potřebuje manifesty všech měsíců a k nim `refunds`
    # z curated -- objem storn je jediné, co o odmítnutých řádcích říká peníze, a
    # v manifestu není. Mapa ani denní řady tu nemají co dělat.
    "quarantine.html": (
        "quarantine",
        "Karanténa NYC Taxi: vyřazené řádky",
        "Které řádky datasetu newyorských taxíků neprošly, měsíc po měsíci: důvod,"
        " počet, podíl a objem storn.",
        ("key", "year", "month", "rows", "refunds", "runs"),
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
    "max_reversal_ratio",
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


def backfill_reversals(run: dict) -> dict:
    """Manifesty jsou append-only a starší běhy storna neznají: měly je pod pravidlem
    `nonpositive_total` a počítaly je do karantény. Přepsat je nejde a zahodit by
    znamenalo díru v řadě, tak se dopočítají do nové podoby.

    Překlad je přesný v penězích a v počtu karantény, ale nadhodnocuje storna o jízdy
    s nulovou útratou (v 2025-01 jich je 559 z 63 596, tedy 0,9 %) -- ty se ve starém
    schématu do stejného čísla schovaly a zpětně je z manifestu nerozpleteš. Stránka
    proto starší běhy značí jako dopočítané.
    """
    rows, rules = run.get("rows", {}), run.get("rules", {})
    if "reversed" in rows or "nonpositive_total" not in rules:
        return run

    reversed_rows = rules.pop("nonpositive_total")
    rules["reversal"] = reversed_rows
    rows["reversed"] = reversed_rows
    rows["rejected"] = rows.get("rejected", 0) - reversed_rows
    run["reversals_estimated"] = True
    return run


CHECK_LIMIT = 14


def read_checks(layout: storage.Layout) -> dict:
    """Posledních pár kontrol, nejnovější první, plus kolik jich celkem je.

    Jméno souboru začíná časovým razítkem, takže poslední se dají vybrat z výpisu a
    stahovat celou historii kvůli čtrnácti řádkům není potřeba. Kontroly přibývají denně,
    manifesty jen při změně dat -- proto je limit vlastní, ne sdílený s běhy.
    """
    names = [name for name in storage.list_names(layout.checks_dir()) if name.endswith(".json")]
    recent = [
        storage.read_json(storage.join(layout.checks_dir(), name))
        for name in reversed(names[-CHECK_LIMIT:])
    ]
    return {"total": len(names), "recent": recent}


def read_runs(layout: storage.Layout, year: int, month: int) -> list[dict]:
    """Manifesty partition, nejnovější první. Append-only: partition se přepisuje,
    manifesty přibývají."""
    runs_dir = layout.runs_dir(year, month)
    runs = [
        backfill_reversals(storage.read_json(storage.join(runs_dir, name)))
        for name in storage.list_names(runs_dir)
        if name.endswith(".json")
    ]
    return sorted(runs, key=lambda r: r["run_id"], reverse=True)


def zone_sums(frame: pl.DataFrame) -> pl.DataFrame:
    """Jeden řádek na zónu za ten měsíc: sčitatelné součty a jejich jmenovatele."""
    return frame.group_by("location_id", "borough", "zone").agg(*SUM_AGGREGATES, *MEDIAN_AGGREGATES)


def daily_sums(frame: pl.DataFrame) -> pl.DataFrame:
    """Totéž po dnech, přes všechny zóny. Tytéž agregace, aby denní křivka a mapa nemohly
    počítat průměr každá po svém."""
    return frame.group_by("date").agg(*SUM_AGGREGATES).sort("date")


def columns(frame: pl.DataFrame, names: tuple[str, ...]) -> dict[str, list]:
    """Sloupcově, ne po řádcích. Jména polí by se ve slovníku na řádek opakovala pro
    každou z 261 zón v každém z měsíců a byla by to většina payloadu; takhle jsou
    v souboru jednou. Cenou je, že se pole musí držet zarovnaná -- proto je pořadí zón
    jedno sdílené (`map.ids`) a měsíc do něj jen dosazuje hodnoty."""
    return {name: frame[name].to_list() for name in names}


def zone_index(monthly: list[pl.DataFrame]) -> pl.DataFrame:
    """Pořadí zón, na které se zarovnávají sloupce všech měsíců. Je společné, protože
    zóna, ve které se v březnu nikdo nesvezl, musí mít v březnu index taky -- jinak by
    se pole měsíc od měsíce rozjela."""
    return (
        pl.concat(monthly)
        .group_by("location_id", "borough", "zone")
        .agg(pl.col("trips").sum())
        .sort("trips", descending=True)
        .drop("trips")
    )


def zone_columns(sums: pl.DataFrame, index: pl.DataFrame) -> dict[str, list]:
    """Měsíční součty dosazené do sdíleného pořadí zón. Zóna bez jediné jízdy v tom
    měsíci má nuly, ne null: nula jízd je tvrzení o datech, které jsme udělali. Medián
    zůstává null -- ten se z ničeho spočítat nedá a stránka na to má vlastní stav."""
    joined = index.join(sums, on=["location_id", "borough", "zone"], how="left").with_columns(
        pl.col(SUM_COLUMNS).fill_null(0)
    )
    return columns(joined, SUM_COLUMNS + MEDIAN_COLUMNS)


def check_columns(frame: pl.DataFrame, year: int, month: int) -> None:
    """Curated může být starší než kód. Sloupec přidaný do transformace se do partition
    dostane, až když ji pipeline přepočítá -- a nezměněný ETag ji sám o sobě nepřepočítá
    nikdy. Ať to build řekne rovnou, ne až tracebackem z polars uprostřed `group_by`."""
    missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
    if missing:
        raise SystemExit(
            f"curated {year:04d}-{month:02d} nemá sloupce {missing}: partition je starší"
            " než kód, který ji čte. Přepočítej ji backfillem -- state machine se vstupem"
            ' {"from":"YYYY-MM","to":"YYYY-MM","force":true}.'
        )


def month_payload(layout: storage.Layout, year: int, month: int) -> tuple[dict, pl.DataFrame]:
    frame = storage.scan_parquet(layout.curated_file(year, month)).collect()
    check_columns(frame, year, month)
    daily = daily_sums(frame)

    payload = {
        "key": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "rows": frame.height,
        "trips": int(frame["trips"].sum()),
        "revenue": round(float(frame["yellow_revenue_usd"].sum()), 2),
        "refunds": round(float(frame["refunds_usd"].sum()), 2),
        "net_revenue": round(float(frame["net_revenue_usd"].sum()), 2),
        "runs": read_runs(layout, year, month),
        "daily": {"date": [str(d) for d in daily["date"]]} | columns(daily, SUM_COLUMNS),
    }
    return payload, zone_sums(frame)


def map_payload(index: pl.DataFrame) -> dict:
    """Podklad pro choropleth: obrysy zón a jména k nim. Čísla tu nejsou -- ta nese
    každý měsíc svoje, protože mapa sleduje měsíční přepínač stejně jako zbytek stránky.

    Rozpočet na to je: 261 zón krát 29 měsíců by po řádcích payload nafouklo o megabajt,
    sloupcově to je ~14 kB na měsíc. Zaplatí se to tím, že měsíční seznam nejvytíženějších
    zón v payloadu být nemusí -- žebříček, tabulka i mapa čtou tentýž blok.
    """
    geo = json.loads((HERE / "zones.json").read_text())
    return {
        "width": geo["width"],
        "height": geo["height"],
        "paths": geo["zones"],
        # Jména ze shapefilu pro zóny, které v curated nemají ani řádek: obrys se kreslí,
        # ale číslo k němu není. Zóny s daty se jmenují podle lookupu, který pipeline
        # joinovala -- ta jména jsou níž v `zone`/`borough`.
        "names": geo["names"],
        "ids": index["location_id"].to_list(),
        "zone": index["zone"].to_list(),
        "borough": index["borough"].to_list(),
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

    # Pořadí zón se musí ustálit dřív, než do něj měsíce dosadí svoje sloupce, takže
    # tenhle krok jde až po přečtení všech partition.
    index = zone_index([sums for _, sums in built])
    for payload, sums in built:
        payload["zones"] = zone_columns(sums, index)

    # Co je na všech stránkách: odkud data jsou, kdy se to postavilo, jak čerstvý je
    # zdroj a kdy se na něj pipeline naposledy ptala.
    # Bez URI curated -- jméno bucketu nese číslo AWS účtu a stránky jsou veřejné.
    common = {
        "dataset": "yellow",
        "generated_at": date.today().isoformat(),
        "source": {
            "store": "S3" if layout.curated_uri.startswith("s3://") else "disk",
            "region": os.environ.get("AWS_REGION", "eu-central-1"),
        },
        "config": {key: getattr(cfg, key) for key in CONFIG_KEYS},
        # `record=False`: build se ptá na totéž co běh, ale nic nespouští a role
        # stránky curated jen čte. Zápis kontroly patří pipeline, ne generátoru HTML.
        "freshness": pipeline.check_freshness(cfg, record=False),
        "checks": read_checks(layout),
    }

    style = (HERE / "style.css").read_text()
    out_dir = HERE / "dist"
    out_dir.mkdir(exist_ok=True)

    written = []
    for filename, (name, title, desc, keys) in PAGES.items():
        payload = common | {"months": [{k: m[k] for k in keys} for m in months]}
        if name == "data":
            payload["map"] = map_payload(index)

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
