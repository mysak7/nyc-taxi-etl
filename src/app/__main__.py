"""CLI. Jediný vstupní bod aplikace; logika je v pipeline/transform, ne tady.

    python -m app run --year 2025 --month 1
    python -m app detect --lookback 6
    python -m app detect --from 2024-01 --to 2024-12 --force
    python -m app check-freshness

Výsledek jde jako JSON na stdout, logy na stderr -- výstup `detect` se dá rovnou nacpat
do `jq` nebo do dynamic task mappingu v Airflow.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import pipeline
from .config import Config
from .errors import PipelineError
from .log import log


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app")
    parser.add_argument("--config", help="cesta k TOML s defaulty")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="zpracuj jeden zdrojový měsíc")
    run.add_argument("--year", type=int, required=True)
    run.add_argument("--month", type=int, required=True, choices=range(1, 13), metavar="1-12")
    run.add_argument("--run-id", help="id běhu zvenčí (Airflow run_id, Lambda request id)")
    run.add_argument("--trigger", default="cli")
    run.add_argument("--expected-etag", help="ETag, který viděl detect")

    detect = commands.add_parser("detect", help="které měsíce je potřeba zpracovat")
    detect.add_argument("--lookback", type=int, help="kolik měsíců zpátky kontrolovat")
    detect.add_argument("--from", dest="from_month", metavar="YYYY-MM")
    detect.add_argument("--to", dest="to_month", metavar="YYYY-MM")
    detect.add_argument("--force", action="store_true", help="ignoruj ETag (backfill, incident)")

    commands.add_parser("check-freshness", help="má zdroj data, která nemáme?")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config, lookback_months=getattr(args, "lookback", None))

    try:
        if args.command == "run":
            result = pipeline.run_month(
                cfg,
                args.year,
                args.month,
                run_id=args.run_id,
                trigger=args.trigger,
                expected_etag=args.expected_etag,
            )
        elif args.command == "detect":
            result = pipeline.detect(
                cfg, months=pipeline.month_range(args.from_month, args.to_month), force=args.force
            )
        else:
            result = pipeline.check_freshness(cfg)
    except PipelineError as error:
        log("failed", error=type(error).__name__, message=str(error))
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
