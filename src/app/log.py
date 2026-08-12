from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

_context: dict[str, object] = {}


def bind(**fields) -> None:
    _context.update(fields)


def log(event: str, **fields) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "event": event,
        **_context,
        **fields,
    }
    print(json.dumps(record, default=str), file=sys.stderr, flush=True)
