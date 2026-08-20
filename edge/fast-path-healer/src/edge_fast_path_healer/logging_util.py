from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def log_event(component: str, **fields: object) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "component": component,
        **fields,
    }
    print(json.dumps(payload, default=str), flush=True, file=sys.stdout)
