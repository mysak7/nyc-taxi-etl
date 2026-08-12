"""Handler pro Lambdu. Vlastní modul, aby import Lambdy netahal argparse a naopak.

Lambda container image nespouští argv, ale handler. Výchozí ENTRYPOINT image je proto
CLI (ten příkaz píše člověk) a Lambda si entrypoint přebíjí v ImageConfig:

    entry_point = ["python", "-m", "awslambdaric"]
    command     = ["app.lambda_handler.handler"]
"""

from __future__ import annotations

from . import pipeline
from .config import Config
from .errors import PermanentError

# Vstup exekuce píše člověk s právem `states:StartExecution` -- na tenhle handler tedy
# nesmí platit "vstup je náš". Pět let zpátky je backfill, víc je překlep: rozsah se
# překládá na jeden HTTP HEAD za měsíc, takže "1900-2100" by byl jen dlouhý timeout.
MAX_MONTHS = 60


def handler(event: dict, context=None) -> dict:
    cfg = Config.load(lookback_months=_lookback(event))
    command = event.get("command", "run")
    run_id = getattr(context, "aws_request_id", None)

    if command == "run":
        return pipeline.run_month(
            cfg,
            int(event["year"]),
            int(event["month"]),
            run_id=run_id,
            trigger="lambda",
            expected_etag=event.get("etag"),
        )
    if command == "detect":
        return pipeline.detect(
            cfg,
            months=_months(event),
            force=bool(event.get("force", False)),
        )
    if command == "check-freshness":
        return pipeline.check_freshness(cfg)
    raise ValueError(f"neznámý příkaz: {command}")


def _lookback(event: dict) -> int | None:
    lookback = event.get("lookback")
    return None if lookback is None else min(int(lookback), MAX_MONTHS)


def _months(event: dict) -> list[tuple[int, int]] | None:
    months = pipeline.month_range(event.get("from"), event.get("to"))
    if months is not None and len(months) > MAX_MONTHS:
        raise PermanentError(f"rozsah {len(months)} měsíců je nad limitem {MAX_MONTHS}")
    return months
