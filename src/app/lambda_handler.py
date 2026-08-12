"""Handler pro Lambdu. Vlastní modul, aby import Lambdy netahal argparse a naopak.

Lambda container image nespouští argv, ale handler. Výchozí ENTRYPOINT image je proto
CLI (ten příkaz píše člověk) a Lambda si entrypoint přebíjí v ImageConfig:

    entry_point = ["python", "-m", "awslambdaric"]
    command     = ["app.lambda_handler.handler"]
"""

from __future__ import annotations

from . import pipeline
from .config import Config


def handler(event: dict, context=None) -> dict:
    cfg = Config.load(lookback_months=event.get("lookback"))
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
            months=pipeline.month_range(event.get("from"), event.get("to")),
            force=bool(event.get("force", False)),
        )
    if command == "check-freshness":
        return pipeline.check_freshness(cfg)
    raise ValueError(f"neznámý příkaz: {command}")
