# nyc-taxi-etl

Pipeline nad **NYC Yellow Taxi Trip Records**: stáhne měsíční soubor, zvaliduje ho,
připojí číselník zón a uloží denní agregaci po zónách jako Parquet. Polars, jeden
kontejner, běží na AWS (Lambda + Step Functions), Airflow DAG jako druhá varianta.

Leden 2025 (3 475 226 jízd, 56 MB) zpracuje za 3,5 s včetně stažení zdroje. Výstup má
7 290 řádků a 236 kB.

**Data, metodika, prahy kvality i provozní manifesty jsou na
[taxi.mysak.fun](https://taxi.mysak.fun)** — proč jsou čísla taková, co pravidla
vyhodila a jak běhy dopadly.

## Spuštění

```bash
uv sync
uv run python -m app run --year 2025 --month 1
uv run python -m app detect            # co se na zdroji změnilo (JSON na stdout)
uv run python -m app check-freshness   # nemá zdroj data, která nemáme?
```

Testy a lint:

```bash
uv run pytest -q
uv sync --group airflow && uv run pytest -q   # včetně DagBag testu
uv run ruff check src tests dags
```

Docker (image běží jako non-root, proto `--user` k bind mountu):

```bash
docker build -t nyc-taxi-etl .
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/data:/app/data" \
  nyc-taxi-etl run --year 2025 --month 1
```

Konfigurace: defaulty v [`src/app/config.toml`](src/app/config.toml), přebíjí se env
proměnnými (`APP_CURATED_URI=s3://bucket/curated`) a pak CLI flagy. Cesty jsou URI, takže
`./data/curated` i `s3://bucket/curated` je tentýž parametr.

Nasazení: `cd infra && terraform apply -var image_tag=sha-$(git rev-parse HEAD)`.

## Struktura

```
src/app/  CLI, pipeline, transformace, DQ pravidla, HTTP, URI I/O, Lambda handler
tests/    fixture s ručně spočítanými čísly · invarianty nad reálným vzorkem
dags/     Airflow DAG
infra/    Terraform: S3, ECR, Lambda, Step Functions, čtyři IAM role
web/      build.py staví statické stránky z curated (Cloudflare Pages)
```
