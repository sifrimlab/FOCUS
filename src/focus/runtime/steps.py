"""Create the mutable state of a reporting step."""

import time
from focus.runtime.types import Step


def new_step(
    index: int, total: int, name: str, unit: str | None, items_total: int, cached: bool
) -> Step:
    """Create step counters and capture their opening time in UNIX seconds."""
    return {
        "index": index,
        "total": total,
        "name": name,
        "unit": unit,
        "items_total": items_total,
        "progress": 0,
        "cached": cached,
        "opened_at": time.time(),
        "announced": False,
    }
