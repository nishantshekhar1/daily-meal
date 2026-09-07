"""Pre-generation of meal suggestions for the current time of day.

A plan costs ``1 + N`` LLM generations — minutes on a local model — so waiting
until the user asks means waiting minutes.  A background worker keeps the
current slot's plan generated and cached, and the suggest endpoints serve it
directly when it is still valid.

Freshness is decided by a fingerprint of everything that would change the
outcome (pantry stock, active household, cuisine priority, plan size) rather
than by a TTL: cooking a meal invalidates the cache immediately, while an
untouched pantry keeps it valid indefinitely.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.agents.planner import MealPlannerAgent
from app.agents.planner_nodes import infer_slot
from app.core.config import get_settings
from app.db.session import engine
from app.models import HouseholdMember, MealSlot, StockLot, SuggestionCache
from app.services import preference
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

# One generation at a time: the local model serves requests serially anyway,
# and this makes a user request piggyback on an in-flight prewarm instead of
# queueing a second identical run behind it.
_generation_lock = asyncio.Lock()


def current_slot() -> str:
    """Slot for the current *local* hour — the user's clock, not UTC."""
    return infer_slot(datetime.now().hour).value


def fingerprint(db: Session) -> str:
    """Hash the inputs that would change a plan's outcome."""
    stock = db.exec(
        select(StockLot.ingredient_id, StockLot.quantity)
        .where(StockLot.quantity > 0)
        .order_by(StockLot.ingredient_id)
    ).all()
    members = db.exec(
        select(HouseholdMember.id)
        .where(HouseholdMember.active == True)  # noqa: E712
        .order_by(HouseholdMember.id)
    ).all()
    settings = get_settings()

    payload = json.dumps(
        {
            # Round quantities: float noise from deductions must not churn the key.
            "stock": sorted((int(i), round(float(q), 3)) for i, q in stock),
            "members": sorted(int(m) for m in members),
            "cuisines": settings.cuisine_priority,
            "size": settings.max_suggestions,
            # Toggling web search (or switching provider) changes what gets
            # shortlisted, so a plan generated under the old setting is stale.
            "web": settings.web_search_ready and settings.web_search_provider,
            # Learned preference feeds both ranking and the shortlist prompt.
            # Without it here, a plan cached before the household rated
            # anything would keep being served until the pantry happened to
            # change — the feedback would be recorded and never take effect.
            "preferences": preference.profile_version(db),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def read_cache(db: Session, slot: str) -> Optional[dict[str, Any]]:
    """Return the cached result for ``slot`` if it matches current inputs."""
    entry = db.get(SuggestionCache, slot)
    if entry is None:
        return None
    if entry.fingerprint != fingerprint(db):
        logger.info("Prewarm cache for %s is stale (pantry/household changed)", slot)
        return None
    try:
        result = json.loads(entry.result_json)
    except json.JSONDecodeError:
        logger.warning("Prewarm cache for %s is corrupt; ignoring", slot)
        return None
    result["cached"] = True
    result["generated_at"] = entry.generated_at.isoformat()
    return result


def write_cache(db: Session, slot: str, result: dict[str, Any]) -> None:
    entry = db.get(SuggestionCache, slot)
    payload = {k: v for k, v in result.items() if k not in ("cached", "generated_at")}
    if entry is None:
        entry = SuggestionCache(slot=slot, fingerprint="", result_json="")
    entry.fingerprint = fingerprint(db)
    entry.result_json = json.dumps(payload, default=str)
    entry.plan_id = result.get("plan_id")
    entry.generated_at = datetime.utcnow()
    db.add(entry)
    db.commit()


def invalidate(db: Session, slot: Optional[str] = None) -> None:
    """Drop cached plans. Rarely needed — the fingerprint handles staleness."""
    entries = (
        [db.get(SuggestionCache, slot)] if slot else db.exec(select(SuggestionCache)).all()
    )
    for entry in entries:
        if entry is not None:
            db.delete(entry)
    db.commit()


async def generate(slot: str, *, force: bool = False) -> dict[str, Any]:
    """Generate and cache a plan for ``slot``.

    Serialized on ``_generation_lock``; a caller that arrives while a run is
    in flight waits for it and then finds the cache warm, rather than starting
    a duplicate generation.
    """
    async with _generation_lock:
        with Session(engine) as db:
            if not force:
                cached = read_cache(db, slot)
                if cached is not None:
                    return cached

            logger.info("Generating suggestions for %s…", slot)
            started = datetime.utcnow()
            agent = MealPlannerAgent(db, get_llm_client())
            result = await agent.suggest(slot=MealSlot(slot))
            elapsed = (datetime.utcnow() - started).total_seconds()

            # Only cache a usable plan: errors and clarification prompts depend
            # on the session the user is in, so they must not be replayed.
            if result.get("error") or not result.get("planned_meals"):
                logger.info("Not caching %s: no usable plan (%.0fs)", slot, elapsed)
                return result

            write_cache(db, slot, result)
            logger.info(
                "Cached %d suggestion(s) for %s in %.0fs",
                len(result["planned_meals"]),
                slot,
                elapsed,
            )
            return read_cache(db, slot) or result


async def ensure_warm(slot: Optional[str] = None) -> None:
    """Generate a plan for ``slot`` unless a valid one is already cached."""
    slot = slot or current_slot()
    with Session(engine) as db:
        if read_cache(db, slot) is not None:
            return
    await generate(slot)


async def prewarm_loop(stop: asyncio.Event) -> None:
    """Background worker: keep the current slot warm until shutdown.

    Re-checks on an interval so it picks up both the clock crossing into a new
    slot and a pantry change that invalidated the fingerprint.
    """
    settings = get_settings()
    interval = settings.prewarm_interval_s
    logger.info("Suggestion prewarm enabled (every %ds)", interval)

    while not stop.is_set():
        try:
            await ensure_warm()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a bad run kill the worker; try again next interval.
            logger.exception("Prewarm run failed")

        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
