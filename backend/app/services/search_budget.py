"""Daily cap on outbound web searches.

Tavily bills per search and free/trial keys have a small monthly allowance, so
the planner must not be able to spend it unattended.  Every search goes through
:func:`try_consume`, which refuses once the day's allowance is gone.

State lives in a small JSON file under ``data_dir`` rather than in memory: a
restart or dev reload must not hand out a fresh quota, since restarts are
exactly when a runaway loop would reset itself.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _path() -> Path:
    return Path(get_settings().data_dir) / "search_budget.json"


def _read() -> dict[str, Any]:
    try:
        with open(_path()) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _today() -> str:
    return date.today().isoformat()


def usage() -> tuple[int, int]:
    """Return ``(used_today, limit)``."""
    limit = get_settings().web_search_daily_limit
    data = _read()
    used = int(data.get("count", 0)) if data.get("day") == _today() else 0
    return used, limit


def try_consume() -> bool:
    """Claim one search against today's allowance.

    Returns False when the allowance is spent, in which case the caller must
    skip the search rather than queue or retry it.
    """
    limit = get_settings().web_search_daily_limit
    if limit <= 0:
        return False

    with _lock:
        data = _read()
        used = int(data.get("count", 0)) if data.get("day") == _today() else 0
        if used >= limit:
            return False

        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"day": _today(), "count": used + 1}, f)

        remaining = limit - (used + 1)
        if remaining <= 3:
            logger.warning("Web search budget nearly spent: %d left today", remaining)
        return True
