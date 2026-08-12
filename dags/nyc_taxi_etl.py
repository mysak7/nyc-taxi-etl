"""Denní orchestrace ETL nad NYC Yellow Taxi.

Zdroj publikuje měsíčně s lagem 26-85 dní a soubory **zpětně přepisuje** (2026-03-25
přepsal prosinec, leden i únor naráz). Denní běh proto nezpracovává "včerejšek", ale
zjišťuje, co se na zdroji změnilo: šest HTTP HEAD requestů a porovnání ETagů. Většina
běhů tak neudělá žádnou práci.

Business logika je v aplikaci (`python -m app`), tady je jen orchestrace: DAG spouští
hotový image jako Lambdu a rozhoduje, co při jakém selhání dělat.

Backfill je ruční trigger téhož DAGu s params:

    {"from": "2024-01", "to": "2024-12", "force": true}

Psáno pro Airflow 3.x; na 2.11 by se změnily jen importy dekorátorů.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

from airflow.providers.amazon.aws.hooks.lambda_function import LambdaHook
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.task.trigger_rule import TriggerRule

FUNCTION_NAME = os.environ.get("NYC_TAXI_FUNCTION", "nyc-taxi-etl")
AWS_CONN_ID = os.environ.get("NYC_TAXI_AWS_CONN", "aws_default")

# Opakovat má smysl jen přechodné chyby. Nepublikovaný měsíc ani překročený DQ práh se
# opakováním nespraví -- retry by stáhl totéž a spadl stejně.
FAIL_FAST = {"PermanentError", "DataQualityError"}


def invoke(command: str, **fields) -> dict:
    """Jediné, co DAG o aplikaci ví: jméno příkazu a tvar odpovědi."""
    context = get_current_context()
    payload = {"command": command, "run_id": context["run_id"], **fields}
    response = LambdaHook(aws_conn_id=AWS_CONN_ID).invoke_lambda(
        function_name=FUNCTION_NAME, payload=json.dumps(payload)
    )
    body = json.loads(response["Payload"].read())
    if "FunctionError" not in response:
        return body

    error_type, message = body.get("errorType", "Unknown"), body.get("errorMessage", "")
    if error_type in FAIL_FAST:
        raise AirflowFailException(f"{error_type}: {message}")
    raise RuntimeError(f"{error_type}: {message}")  # přechodná -> ať to retry zkusí znovu


def alert(context) -> None:
    """Volá se až když task vyčerpá retries, ne při každém pokusu. Skipnuté tasky (žádná
    změna ETagu) neposílají nic -- jinak by po týdnu všichni alerty zafiltrovali."""
    task_instance = context["task_instance"]
    message = (
        f"{context['dag'].dag_id} / {task_instance.task_id} selhal"
        f" (run {context['run_id']}, pokus {task_instance.try_number}): {context.get('reason', '')}"
        f"\nlog: {task_instance.log_url}"
    )
    print(message)  # cíl je Airflow Connection (Slack/SNS), ne hardcode v DAGu


@dag(
    dag_id="nyc_taxi_etl",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,  # denní interval nemá nic společného s tím, který měsíc dat se zpracovává
    max_active_runs=1,
    tags=["nyc-taxi", "etl"],
    params={"from": None, "to": None, "force": False},
    on_failure_callback=alert,
    default_args={"owner": "data-platform", "retries": 0},
    doc_md=__doc__,
)
def nyc_taxi_etl():
    @task(retries=2, retry_delay=timedelta(minutes=2), retry_exponential_backoff=True)
    def detect_changed_months() -> list[dict]:
        """Kolik je práce, se ví až za běhu: nula, jeden měsíc, nebo tři při restatementu."""
        params = get_current_context()["params"]
        result = invoke(
            "detect", **{"from": params["from"], "to": params["to"], "force": params["force"]}
        )
        print(json.dumps(result, indent=2))
        return result["months"]

    @task(
        max_active_tis_per_dag=2,  # zdroj je CloudFront, ne náš server, ale slušnost stojí nula
        retries=2,
        retry_delay=timedelta(minutes=5),
        retry_exponential_backoff=True,
        execution_timeout=timedelta(minutes=20),  # zaseknutý download nedrží slot navždy
    )
    def process(month: dict) -> dict:
        """Selhaný březen neshodí leden -- každý měsíc je vlastní mapped task."""
        return invoke("run", year=month["year"], month=month["month"], etag=month.get("etag"))

    @task(trigger_rule=TriggerRule.NONE_FAILED)
    def check_freshness() -> dict:
        """Musí běžet i ve dnech, kdy nebyla žádná práce -- právě tehdy je totiž zelený
        DAG nejvíc podezřelý (proto NONE_FAILED, ne ALL_SUCCESS)."""
        return invoke("check-freshness")

    process.expand(month=detect_changed_months()) >> check_freshness()


nyc_taxi_etl()
