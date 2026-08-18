# nyc-taxi-etl

Pipeline nad **NYC Yellow Taxi Trip Records**: stáhne měsíční soubor, zvaliduje ho,
připojí číselník zón a uloží denní agregaci po zónách jako Parquet. Polars, jeden
kontejner, běží na AWS (Lambda + Step Functions).

**Data, metodika, prahy kvality i provozní manifesty jsou na
[taxi.mysak.fun](https://taxi.mysak.fun)** — proč jsou čísla taková, co pravidla
vyhodila a jak běhy dopadly.

## Spuštění

```bash
uv sync
uv run python -m app run --year 2025 --month 1
uv run python -m app --help    # detect, check-freshness
uv run pytest -q
```

Konfigurace: defaulty v [`src/app/config.toml`](src/app/config.toml), přebíjí se env
proměnnými (`APP_CURATED_URI=s3://bucket/curated`) a pak CLI flagy.

Nasazení: `cd infra && terraform apply -var image_tag=sha-$(git rev-parse HEAD)`.

## Struktura

```
src/app/  CLI, pipeline, transformace, DQ pravidla, HTTP, URI I/O, Lambda handler
tests/    fixture s ručně spočítanými čísly · invarianty nad reálným vzorkem
dags/     Airflow DAG
infra/    Terraform: S3, ECR, Lambda, Step Functions, IAM
web/      build.py staví statické stránky z curated (Cloudflare Pages)
```

## Data a licence

Zdroj je [NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)
včetně číselníku a shapefilu zón; vzorky v `tests/data/` jsou z něj odvozené. Kód je pod
[MIT](LICENSE).
