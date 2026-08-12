"""Import test DAGu: levné, chytí syntax chyby, špatné importy a cykly. Airflow je jen
v dev skupině (~200 MB), do image nepatří."""

import pytest

pytest.importorskip("airflow", reason="uv sync --group airflow")


@pytest.fixture(scope="module")
def dag():
    from airflow.models import DagBag

    bag = DagBag(dag_folder="dags")  # v Airflow 3 už DagBag příklady nenabírá
    assert bag.import_errors == {}
    return bag.dags["nyc_taxi_etl"]


def test_dag_se_naimportuje_a_ma_ocekavane_tasky(dag):
    assert set(dag.task_ids) == {"detect_changed_months", "process", "check_freshness"}


def test_backfill_neni_druhy_dag_ale_params(dag):
    assert set(dag.params) == {"from", "to", "force"}
    assert dag.catchup is False  # jinak by vznikly stovky běhů, které dělají tentýž HEAD


def test_retry_politika_odpovida_typu_prace(dag):
    assert dag.get_task("process").retries == 2
    assert dag.get_task("process").execution_timeout is not None
    assert dag.on_failure_callback is not None
