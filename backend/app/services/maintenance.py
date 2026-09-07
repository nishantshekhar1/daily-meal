"""Background worker that closes the feedback loop.

Three jobs on one schedule, in dependency order:

1. Sweep suggestions that were shown, never cooked and never commented on.
   This is what turns inaction into evidence, and it is the only signal that
   requires the passage of time to observe.
2. Re-aggregate preference profiles from the full event log.
3. Snapshot quality metrics so the trend survives.

Deliberately infrequent (six hours by default). None of the inputs move faster
than that, and on a single-GPU box this worker competes with the model.

Modelled on :func:`app.services.prewarm.prewarm_loop`, including its rule that
one bad run must never kill the worker.
"""
from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session

from app.core.config import get_settings
from app.db.session import engine
from app.services import feedback as feedback_svc
from app.services import metrics as metrics_svc
from app.services import preference as preference_svc

logger = logging.getLogger(__name__)


def run_once(db: Session) -> dict[str, int]:
    """One maintenance pass. Synchronous and safe to call from a route or test."""
    swept = feedback_svc.sweep_skipped(db)
    profiles = preference_svc.rebuild_profiles(db)
    snapshot = metrics_svc.snapshot(db, get_settings().metrics_window_days)
    return {
        "swept": swept,
        "profiles": profiles,
        "suggestions_measured": snapshot.suggestions_count,
    }


async def maintenance_loop(stop: asyncio.Event) -> None:
    """Run :func:`run_once` on an interval until shutdown."""
    interval = get_settings().feedback_maintenance_interval_s
    logger.info("Feedback maintenance enabled (every %ds)", interval)

    while not stop.is_set():
        try:
            with Session(engine) as db:
                summary = run_once(db)
            logger.info(
                "Feedback maintenance: swept %d, %d profile(s), %d suggestion(s) measured",
                summary["swept"],
                summary["profiles"],
                summary["suggestions_measured"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a bad run kill the worker; try again next interval.
            logger.exception("Feedback maintenance run failed")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
