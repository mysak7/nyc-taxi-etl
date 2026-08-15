# nyc-taxi-etl

Pipeline nad **NYC Yellow Taxi Trip Records**: stáhne měsíční soubor, zvaliduje ho,
připojí číselník zón a uloží denní agregaci po zónách jako Parquet. Polars, jeden
kontejner, Airflow DAG pro denní provoz.

Celý leden 2025 (3 475 226 jízd, 56 MB) zpracuje za **4,9 s**, výstup má 7 290 řádků
a 232 kB.

> Poznámka ke zadání: jako vstupní dataset je uvedena *Taxi Zone Lookup Table*, ale
> požadovaný výstup (počty jízd, tržby) a parametrizace `--year/--month` z 265řádkového
> číselníku spočítat nejdou. Čtu to jako fakta × dimenze: fakta jsou
> `yellow_tripdata_YYYY-MM.parquet`, číselník je dimenze, join přes `PULocationID`.

## Spuštění

```bash
uv sync                                      # reprodukovatelná instalace z uv.lock
uv run python -m app run --year 2025 --month 1
uv run python -m app detect                  # co se na zdroji změnilo (JSON na stdout)
uv run python -m app check-freshness         # nemá zdroj data, která nemáme?
```

Testy (30 testů, 3 s; Airflow jen pro import test DAGu):

```bash
uv run pytest -q
uv sync --group airflow && uv run pytest -q  # včetně DagBag testu
uv run ruff check src tests dags
```

Docker:

```bash
docker build -t nyc-taxi-etl .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data:/app/data" \
  nyc-taxi-etl run --year 2025 --month 1
```

`--user` je potřeba, protože image běží jako non-root (uid 10001) a bind mount patří
hostiteli. Bez něj zápis do `data/` selže na právech.

Konfigurace: defaulty v [`src/app/config.toml`](src/app/config.toml), přebíjí se env
proměnnými (`APP_CURATED_URI=s3://bucket/curated`) a pak CLI flagy. Cesty jsou URI, ne
`Path` — `./data/curated` i `s3://bucket/curated` je tentýž parametr.

## Datový model

Jeden řádek = **(den nástupu, zóna nástupu)**. Atribuce podle `PULocationID`, protože to
je místo, kde vznikla poptávka.

| sloupec | pozn. |
|---|---|
| `date`, `location_id`, `borough`, `zone`, `service_zone` | dimenze; `location_id` zůstává pro join zpět |
| `trips` | počet publikovaných jízd |
| `avg_distance_mi`, `median_distance_mi`, `distance_obs` | průměr, medián a **jmenovatel** |
| `avg_duration_min`, `median_duration_min`, `duration_obs` | doba jízdy ve zdroji není, počítá se z razítek |
| `avg_fare_usd`, `fare_obs` | |
| `yellow_revenue_usd` | hrubá tržba (`total_amount`) |
| `refunds_usd` | storna, záporné znaménko |
| `net_revenue_usd` | hrubá tržba po stornech — číslo, které se vykazuje |

Výstup: `curated/dataset=yellow/year=2025/month=01/yellow_taxi_trips_by_zone.parquet`.
Celá historie 29 měsíců je ~4,6 MB, přečte se jedním
`scan_parquet("curated/**/*.parquet")` — proto tu není žádná databáze.

## Rozhodnutí a proč

Každé je podložené měřením na skutečných datech, ne odhadem.

- **Denní schedule, práci řídí ETag.** Zdroj publikuje s lagem **26–85 dní** a soubory
  **zpětně přepisuje**: 25. 3. 2026 v 15:46–15:55 přepsal prosinec, leden i únor naráz.
  Denní běh proto nezpracovává „včerejšek", ale dělá 6 HEAD requestů a porovná ETagy.
  Většina běhů neudělá nic.
- **Okno 6 měsíců, končí u předchozího měsíce.** Nejnovější publikovaný měsíc je dnes
  2026-05, tedy tři měsíce zpátky. Okno „poslední 3 měsíce" by obsahovalo jen
  nepublikované měsíce (403) a pipeline by nezpracovala nikdy nic.
- **Jednotka idempotence je zdrojový měsíc, ne den.** Lednový soubor obsahuje 22 jízd
  z prosince a února. Partitioning podle `pickup_date` by rozbil atomicitu přepisu.
  Jedna partition = **jeden soubor** → přepis je `os.replace` lokálně a jedno `PUT` na S3,
  takže nikdy nevznikne partition se soubory ze dvou verzí zdroje.
- **Účtenka je fakt, měření jsou atributy.** Do karantény jde řádek jen když z něj neplyne
  kladná útrata (1,83 %). Vadná *hodnota* se nuluje a nezapočítá se do svého průměru, ale
  řádek ani jeho peníze nevyhazuje: vyhazování celých řádků by stálo **2,32 mil. USD tržeb
  a 88 743 jízd** a průměrnou vzdálenost by změnilo o **0,1 %** (3,166 vs 3,169 mil).
- **Každý průměr nese svůj jmenovatel.** Globálně má vzdálenost jmenovatel 97,5 %, ale
  Newark Airport 3. 1. 2025 má 28 jízd a **jediné použitelné měření vzdálenosti**. Jedno
  globální číslo v reportu by tohle nepopsalo, proto `*_obs` sloupce ve výstupu.
- **Medián vedle průměru.** 122 řádků z 3,5 milionu (0,0035 %) nese **47 % všech ujetých
  mil** — nejdelší „jízda" má 276 423 mil, obvod Země je 24 901. Naivní průměr je o 89 %
  nadstřelený, medián (1,67 mil) se nehne.
- **Razítka jsou naivní newyorský místní čas, ne UTC.** Doloženo: 9. 3. 2025 mezi 02:00
  a 03:00 je v datech nula jízd (přechod na letní čas), běžná neděle má ve stejnou hodinu
  4 809. Převod na UTC by posunul jízdy kolem půlnoci do špatného dne a nic by to
  nechytilo. Přiznaná limitace: doby jízd přes tu hodinu jsou posunuté o ±1 h.
- **Polars lazy, ale žádný streaming.** Naměřeno: 20 sloupců = 467 MB v paměti, 6
  potřebných = **146 MB**. `scan_parquet` + jeden `collect()` dá projection pushdown
  zadarmo; streaming engine, chunkování, Spark ani DuckDB by tady byly jen dekorace.
- **Pokrytí je Yellow Medallion Taxi = 12,4 % tržeb za jízdy v NYC** (leden 2025, proti
  HVFHV/Green/FHV), mimo Manhattan pod 1,5 %. Proto se tabulka jmenuje
  `yellow_taxi_trips_by_zone` a sloupec `yellow_revenue_usd` — na otázku „kolik se utratilo
  za taxi v Bronxu" odpoví tenhle výstup 122× nižším číslem, než je skutečnost, a špatný
  by byl jen název, který svádí číst ho jako celý trh.

## Data quality

Vlastní deklarativní pravidla ([`src/app/dq.py`](src/app/dq.py)), žádná další závislost.
Dvě severity: **kontrakt schématu** padá tvrdě *před* transformací, **kvalita řádku** jde
do karantény nebo vynuluje pole.

Kontrakt je „povinný sloupec existuje a je toho druhu", ne „schéma se rovná" — drift je
doložený: `cbd_congestion_fee` v 2024-01 chybí, od 2025-01 je (19 → 20 sloupců). Nový
sloupec proto není důvod k pádu, ale jde do manifestu.

Naměřeno na 2025-01 (3 475 226 řádků), prahy mají rezervu z měření:

| pravidlo | řádků | co se stane | práh |
|---|---|---|---|
| `total_amount <= 0` | 63 596 (1,83 %) | karanténa; objem do `refunds_usd` a odečtený v `net_revenue_usd` | karanténa > 20 % → fail |
| pickup mimo měsíc | 22 | karanténa (partitioning) | |
| `fare_amount < 0` | 144 118 | vynulovat `fare_amount` | |
| `trip_distance <= 0` / `> 300 mi` | 90 893 / 118 | vynulovat `trip_distance` | > 10 % → fail |
| rychlost `> 100 mph` | 254 | vynulovat `trip_distance` | (týž práh) |
| doba `<= 0` / `> 8 h` | 2 051 / 1 135 | vynulovat `duration_min` | > 5 % → fail |
| objem proti předchozímu měsíci | — | — | ±40 % → fail |

Vzdálenost se **neposuzuje magnitudou, ale nepoměrem k době**. Dřívější práh 200 mil
dělal obě chyby naráz: minul 136 rozbitých jízd pod prahem (165,91 mil za 11,4 minuty za
17,70 $) a zároveň vynuloval 2 reálné dálkové (206 a 225 mil při 53–59 mph, jízdné 220 a
400 $ v tarifu 5). Ze 122 jízd nad 200 mil jich 118 poruší i rychlostní pravidlo, takže
záchyt tím neklesl. Že je vadné pole *tachometr* a ne razítko, plyne z peněz: u 93 %
řádků nad 100 mph sedí jízdné na dobu, ne na vzdálenost — medián 14,46 $ proti 301 $,
které by odpovídaly naměřené vzdálenosti. Magnituda zůstává jako záloha pro řádky, kde je
doba pod minutu a podíl nic neznamená.

Práh ±40 % vychází z 29 změřených měsíců: největší skutečný skok byl +21,9 % (2024-09),
největší pokles −13,5 % (2026-01). Vyřazené řádky nejdou do koše, ale do
`rejects/…/rejects.parquet` **s důvodem** — v regulovaném prostředí musí jít říct *které*
řádky vypadly. Počty pravidel se v reportu počítají **nezávisle** (řádek může porušit víc
pravidel), takže čísla nezávisí na pořadí.

Vedle výstupu leží append-only manifest `_runs/<run_id>.json`: ETag a `sha256` zdroje,
počty řádků, která pravidla se spustila, prahy, které tehdy platily, a viděné schéma.
Partition se přepisuje, manifesty přibývají — „leden byl přepočítán třikrát, tady jsou
ETagy a počty" je pak dotaz na `ls`.

## Provoz

DAG [`dags/nyc_taxi_etl.py`](dags/nyc_taxi_etl.py) (Airflow 3.x): `detect_changed_months`
→ `process.expand(month=…)` → `check_freshness`.

- **Dynamic task mapping**, protože kolik je práce, se ví až za běhu: nula, jeden měsíc,
  nebo tři při restatementu. Prázdný seznam = DAG skončí zeleně za pár sekund. Selhaný
  březen neshodí leden.
- **Business logika není v DAGu.** `detect` je podpříkaz *téže* aplikace, ne task s `httpx`
  v DAGu — jinak by v DAGu skončilo lookback okno, porovnání ETagů i znalost URL zdroje.
  DAG spouští hotový image jako Lambdu; worker nenese Polars a upgrade Polars neshodí
  scheduler. Proto ne `PythonOperator`.
- **`catchup=False`, backfill je ruční trigger s params** `{"from": "2024-01", "to":
  "2024-12", "force": true}`. Denní interval nemá nic společného s tím, který měsíc dat se
  zpracovává; `catchup=True` by vyrobil stovky běhů, které dělají tentýž HEAD. `force`
  slouží i k „přepočti mi leden" ve tři ráno. Žádný druhý DAG.
- **Zelený DAG nesmí lhát.** `check_freshness` má dvě tvrzení: (1) *ingest gap* — zdroj má
  publikovaný měsíc, pro který není výstup → fail bez časového prahu; (2) *staleness* —
  nejnovější publikovaný měsíc je starší než 120 dní → „TLC nepublikovala". Dnes je to
  číslo 72 dní, takže práh 75 dní (naivní volba podle lagu) by svítil červeně na zdravém
  zdroji. Běží i ve dnech bez práce (`NONE_FAILED`) — právě tehdy je zelený DAG nejvíc
  podezřelý.
- **Retry jen na přechodné chyby.** Tři selhání vypadají stejně (červený task), opakovat má
  smysl jedno:

  | chyba | typ | chování |
  |---|---|---|
  | 5xx, timeout, reset | `TransientError` | retry 2× s exponenciálním backoffem |
  | 403/404 (měsíc není publikovaný) | `PermanentError` | fail-fast; `detect` takový měsíc do práce vůbec nedá |
  | překročený DQ práh | `DataQualityError` | fail-fast — retry stáhne totéž a spadne stejně |

  Default `retries=3` na všechno by znamenal, že nepublikovaný měsíc umírá 45 minut a
  rozbitá data se počítají čtyřikrát. K tomu `execution_timeout`, aby zaseknutý download
  nedržel slot navždy.
- **Alert až po vyčerpání retries**, jedním kanálem (Airflow Connection, ne hardcode).
  Skipnuté tasky neposílají nic — jinak by po týdnu všichni alerty zafiltrovali a nevšimli
  si toho jednoho skutečného incidentu.
- **Stav není nikde zvlášť.** Vedle raw souboru leží `_meta.json` (ETag, Last-Modified,
  sha256); `detect` porovnává HEAD proti němu. Stav *je* to, co je zapsané, takže se
  s realitou nemůže rozejít a nemá vlastní recovery postup. Chybí-li sidecar, měsíc se
  prostě zpracuje znovu — zápis je idempotentní. Airflow Variable / XCom by stav svázalo
  s Airflow.
- **Logy jsou JSON na stderr** se sdíleným kontextem (`run_id`, `year`, `month`), na stdout
  jde jen výsledek — výstup `detect` se dá poslat rovnou do `jq` nebo do mapped tasku.

## Nasazení

Běží na AWS ([`infra/`](infra), ~450 řádků Terraformu). **Týž image jako lokálně** jako
Lambda container image, orchestrace ve Step Functions, spouštění EventBridge Schedulerem.
Naměřeno na deployi: **15,3 mil. řádků ve 4 měsících za 22 s** wall-clock (souběh 2),
prázdný běh 3,4 s.

```bash
cd infra && terraform apply -var image_tag=sha-$(git rev-parse HEAD)
aws stepfunctions start-execution --state-machine-arn <arn> --input '{}'
aws stepfunctions start-execution --state-machine-arn <arn> \
  --input '{"from":"2024-01","to":"2024-12","force":true}'   # backfill, týž stroj
```

Stavový stroj je ten samý graf jako DAG: `detect` → `Map` přes změněné měsíce (souběh 2,
retry jen na `TransientError`) → `check-freshness`. Dva rozdíly proti naivnímu překladu:
prázdný seznam měsíců Map přeskočí (většina běhů, 3 s), a **zelená exekuce nesmí lhát** —
freshness sama nestačí, protože měsíc s novým ETagem, kterému spadl přepočet, má na S3
pořád starý výstup a žádnou mezeru nedělá. Selhané měsíce se proto sbírají v `Map` a
vyhodnotí na konci; jeden spadlý březen ale nezruší leden, který čeká ve frontě.

Lambda container image nespouští `argv`, ale handler ([`lambda_handler.py`](src/app/lambda_handler.py)).
Výchozí `ENTRYPOINT` image je proto CLI, protože ten příkaz píše člověk; Lambda si
entrypoint přebíjí ve své `ImageConfig` na `python -m awslambdaric`. V image tím nezůstává
žádný kompromis.

Airflow DAG zůstává jako druhá varianta pro prostředí, kde Airflow už je — obojí jen jinak
spouští týž kontejner, business logika je v aplikaci. Nasazují se navzájem výlučně.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): ruff → pytest (včetně DagBag
testu) → build multi-stage image (base pinnutý digestem, non-root) → push do GHCR
(`sha-<git sha>`, `latest`) a do ECR (jen `sha-`, repozitář je `IMMUTABLE`) →
`update-function-code`. **Žádný AWS klíč v GitHub Secrets**: role se přebírá přes OIDC
a důvěřuje jen jedné větvi jednoho repozitáře.

### Kdo co smí

Čtyři role, žádná nemá „spouštět i měnit". Hranice jsou ověřené
`iam simulate-principal-policy`, ne jen napsané:

| role | smí | nesmí |
|---|---|---|
| `-lambda` | `GetObject`/`PutObject` na třech prefixech, zapsat log | **explicitní Deny na `DeleteObject`** — pipeline nikdy nemaže, přepis je Put přes týž klíč |
| `-states` | `InvokeFunction` na jedné funkci | cokoli s daty |
| `-ci` | push do jednoho ECR repa, `UpdateFunctionCode` | `UpdateFunctionConfiguration` — jinak by šlo přes `APP_*_URI` odklonit pipeline jinam bez změny v image |
| `-operator` | `StartExecution`, číst logy a `curated/` | `InvokeFunction`, `UpdateStateMachine`, `raw/`, zápis kamkoli |

Operátor tedy pipeline spustit může a jediná páka, kterou má, je vstup exekuce: rozsah
měsíců a `force`. Rozsah je omezený na 60 měsíců přímo v handleru — vstup exekuce píše
člověk, takže na něj neplatí „vstup je náš". Role se přebírá jen s MFA a na hodinu.

Bucket: block public access, SSE, verzování (přepis partition je jediný zápis, který se
nedá vzít zpět jinak), `Deny` na non-TLS přístup, lifecycle na staré verze `raw/`. Žádná
VPC — není co do ní dát, jen odchozí HTTPS na CloudFront, a NAT s security groupou by byl
další povrch k rozbití. Alarmy jsou dva: exekuce selhala **a** dva dny žádná nezačala —
„nic neběží" jinak vypadá úplně stejně jako „všechno je v pořádku".

Náklady: 30 běhů měsíčně × ~15 s × 3 GB + S3 + Step Functions ≈ **pod 1 USD/měsíc**.

**Při výrazně větším objemu** se mění spouštěč, ne kód: do jednotek GB/měsíc stačí Lambda
(dnes 56 MB vstupu, 146 MB v paměti, 4,9 s), nad 15 minut běhu týž image na Fargate nebo
Batch. Teprve při rozpadu jednoho měsíce na dny (řádově desítky GB) by přišly paralelní
mapped tasky. Spark by na tomhle objemu byl dekorace, která zdraží provoz i onboarding.

## Co chybí a proč

- **Terraform state je lokální.** Stack aplikuje jeden člověk z jednoho místa; v týmu by
  tu byl S3 backend se zámkem, jinak dva souběžné `apply` přepíšou stav. Backend je
  v [`infra/main.tf`](infra/main.tf) připravený zakomentovaný.
- **Odchozí provoz Lambdy není omezený na CloudFront.** Bez VPC to nejde a VPC by přidala
  NAT a security groupu — víc povrchu než užitku pro funkci, která volá jednu doménu.
- **Karanténa vyřazuje celý řádek**, takže storno vypadne i z `trips`. Objem storn je proto
  ve výstupu zvlášť (`refunds_usd`) a rovnou i odečtený (`net_revenue_usd`) — jen hrubé
  číslo by tržbu přeceňovalo o 1,83 % (2025-01: 90,66 mil. $ hrubě, 1,66 mil. $ storna).
  Storno se přitom účtuje na svůj vlastní den a zónu, ne na den původní jízdy; přes den
  to nevadí (podíl storen je 1,5–2,0 % každý den měsíce, jediná výjimka je 1. leden se
  3,8 %), na úrovni den × zóna ano — ve 14 z 7 290 řádků 2025-01 je čistá tržba záporná,
  v 8 z nich dokonce bez jediné jízdy. Proto zůstávají v curated všechny tři sloupce:
  rozklad si každý spočítá zpátky.
- **`passenger_count` není ve výstupu** (NULL u 15,5 % řádků, Flex Fare). Pravidla mají
  chránit publikované metriky, ne pokrývat „co je v datech divné" — zahodit kvůli němu 540
  tisíc jízd by byla chyba.
- **Rozdíl mezi verzemi téhož měsíce se jen vykazuje**, nehlídá prahem: baseline pro „o
  kolik se leden při přepočtu smí změnit" nikdo nezměřil a jediné vycucané číslo v řešení
  by bylo právě tohle.
- **Raw bytes se přepisují, neverzují.** Lineage funguje odkazem (ETag + sha256 v
  manifestu), ne archivem. Verzování na bucketu je zapnuté, ale staré verze `raw/` mizí po
  7 dnech: je to pojistka proti chybě v kódu, ne archiv zdroje — ten se dá stáhnout znovu.

## Struktura

```
src/app/     __main__.py (CLI) · pipeline.py (orchestrace) · transform.py (čistá logika)
             dq.py (pravidla) · source.py (HTTP) · storage.py (URI I/O) · config.py
             errors.py · log.py · lambda_handler.py
tests/       fixture s ručně spočítanými čísly · kontrakt na dvou verzích schématu
             idempotence zápisu · detekce práce bez sítě · DagBag import test
             s3 větev storage proti fake klientovi (jinak se poprvé spustí až na Lambdě)
dags/        nyc_taxi_etl.py
infra/       main.tf (S3, ECR, Lambda) · orchestration.tf (Step Functions, scheduler,
             alarmy) · iam.tf (čtyři role a jejich hranice)
web/         build.py (staví všechny stránky z curated) · geo.py + zones.json (obrysy zón)
             style.css + common.js (společné) · data.* (co se jezdí) · method.* (proč tak)
             pipeline.* (provoz)
```

Stránka je statická a jsou tři, protože otázky jsou tři. `index.html` odpovídá na „co se
v New Yorku jezdí" (mapa zón, měsíce, průměr vedle mediánu) a je psaná, jako by byla
veřejná produkční stránka — metodické poznámky na ní nejsou. `method.html` odpovídá na
„proč jsou ta čísla taková": šest rozhodnutí z této sekce postavených jako spor (co by
udělal snadný postup, co by stál, co se dělá místo toho), jak se prahy měří a zapisují,
a co výstup pořád neumí. `pipeline.html` odpovídá na „dá se tomu běhu věřit" (manifesty,
prahy, co pravidla chytila).

Rozdělený je i payload — mapa se dostane jen na datovou stránku, manifesty jen na
provozní a metodickou. Argumenty na `method.html` jsou pevný text, ale všechny počty,
prahy a podíly v tabulce pravidel a v grafu rezerv se počítají z manifestů; kde je číslo
změřené jednorázově nad celou historií, věta říká, na kterém měsíci. Nasazuje je
[`.github/workflows/web.yml`](.github/workflows/web.yml) na Cloudflare Pages, po nočním
běhu pipeline, ne jen při pushi.

Fixture testy tvrdí **přesná** čísla spočítaná ručně (spadnou-li po změně logiky, je to
jejich účel), testy nad reálným vzorkem netvrdí žádné konkrétní číslo, jen invarianty
(`karanténa + publikované = vstup`, každé `location_id` se dojoinovalo). Vzorky v repu
vyrobí [`tests/data/make_sample.py`](tests/data/make_sample.py) — jsou vybrané tak, aby
obsahovaly patologie, ne prvních N řádků.
