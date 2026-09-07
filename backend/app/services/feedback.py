"""Capture feedback about suggested meals, explicit and implicit.

Most of the signal here costs the user nothing: the app already records which
meals were cooked, and a plan the user re-rolled is a rejection of everything
in it. Explicit thumbs exist to calibrate those noisier implicit signals, not
to carry the loop on their own.

Everything lands in ``DishFeedback`` as an append-only log. Aggregation into
scores is a separate concern (:mod:`app.services.preference`).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, col, select

from app.models import (
    Dish,
    DishFeedback,
    FeedbackReason,
    FeedbackSignal,
    MealPlan,
    PlannedMeal,
    SuggestionCache,
)

logger = logging.getLogger(__name__)

# How long after a meal was suggested we conclude it was passed over. Long
# enough that an evening plan viewed at noon is not written off prematurely.
SKIP_AFTER_HOURS = 30

_EXPLICIT = (FeedbackSignal.thumbs_up, FeedbackSignal.thumbs_down)


def _dish_context(db: Session, planned_meal_id: int) -> tuple[PlannedMeal, Dish]:
    planned = db.get(PlannedMeal, planned_meal_id)
    if planned is None:
        raise ValueError(f"Planned meal {planned_meal_id} not found")
    dish = db.get(Dish, planned.dish_id)
    if dish is None:
        raise ValueError(f"Dish {planned.dish_id} not found")
    return planned, dish


def _existing_signals(db: Session, planned_meal_id: int) -> list[DishFeedback]:
    return list(
        db.exec(
            select(DishFeedback).where(DishFeedback.planned_meal_id == planned_meal_id)
        ).all()
    )


def record(
    db: Session,
    planned_meal_id: int,
    signal: FeedbackSignal,
    reason: Optional[FeedbackReason] = None,
) -> DishFeedback:
    """Record one feedback event against a suggested meal.

    Explicit ratings replace any earlier explicit rating for the same meal, so
    a user changing their mind corrects the record instead of double-counting.
    Implicit signals are deduplicated by kind for the same reason.
    """
    planned, dish = _dish_context(db, planned_meal_id)

    existing = _existing_signals(db, planned_meal_id)
    if signal in _EXPLICIT:
        superseded = [f for f in existing if f.signal in _EXPLICIT]
    else:
        superseded = [f for f in existing if f.signal == signal]
    for old in superseded:
        db.delete(old)

    event = DishFeedback(
        planned_meal_id=planned_meal_id,
        dish_id=dish.id,
        signal=signal,
        reason=reason,
        audience=planned.audience.value if planned.audience else "main",
        cuisine=dish.cuisine,
        created_at=datetime.utcnow(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    logger.info("Feedback %s recorded for planned meal %d", signal.value, planned_meal_id)
    return event


def record_cooked(db: Session, planned_meal_id: int) -> Optional[DishFeedback]:
    """Log the implicit positive that a meal was actually cooked.

    Called from the cook flow. Failures are swallowed by the caller — losing a
    feedback row must never break marking a meal as cooked.
    """
    return record(db, planned_meal_id, FeedbackSignal.cooked)


def _last_shown_meal_ids(db: Session, slot: str) -> list[int]:
    """Planned meal ids the user was most recently shown for ``slot``.

    Prefers the suggestion cache because that is literally what was on screen.
    Falls back to the newest plan for the slot, which is the only option when
    prewarming is disabled (as it is on the smaller GPU profiles).
    """
    entry = db.get(SuggestionCache, slot)
    if entry is not None:
        try:
            payload = json.loads(entry.result_json)
            ids = [
                m["planned_meal_id"]
                for m in payload.get("planned_meals", [])
                if m.get("planned_meal_id") is not None
            ]
            if ids:
                return ids
        except (ValueError, TypeError, KeyError):
            logger.warning("Could not read cached plan for %s while logging reroll", slot)

    newest = db.exec(
        select(PlannedMeal)
        .join(MealPlan, col(MealPlan.id) == col(PlannedMeal.plan_id))
        .where(col(PlannedMeal.slot) == slot)
        .order_by(col(MealPlan.created_at).desc(), col(PlannedMeal.id).desc())
        .limit(10)
    ).all()
    if not newest:
        return []
    # Keep only the most recent plan's meals, not a mix across plans.
    latest_plan_id = newest[0].plan_id
    return [m.id for m in newest if m.plan_id == latest_plan_id]


def record_reroll(db: Session, slot: str) -> int:
    """Log that the user rejected the whole plan they were shown for ``slot``.

    A weak, batch-level negative: it says the set was not appealing right now,
    not that any single dish is bad. Meals the user already rated explicitly
    are left alone, since a direct opinion outranks an inferred one.

    Returns the number of events recorded.
    """
    recorded = 0
    for planned_meal_id in _last_shown_meal_ids(db, slot):
        existing = _existing_signals(db, planned_meal_id)
        if any(f.signal in _EXPLICIT for f in existing):
            continue
        try:
            record(db, planned_meal_id, FeedbackSignal.rerolled)
            recorded += 1
        except ValueError:
            continue
    if recorded:
        logger.info("Logged reroll against %d suggestion(s) for %s", recorded, slot)
    return recorded


def sweep_skipped(db: Session, *, older_than_hours: int = SKIP_AFTER_HOURS) -> int:
    """Log meals that were suggested, never cooked, and never commented on.

    This is the signal that makes the loop work without nagging: doing nothing
    is itself weak evidence. Only meals with no feedback at all are swept, so
    an explicit rating or a reroll is never overwritten by a weaker inference.

    Returns the number of events recorded.
    """
    cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)

    stale = db.exec(
        select(PlannedMeal)
        .join(MealPlan, col(MealPlan.id) == col(PlannedMeal.plan_id))
        .where(
            col(PlannedMeal.cooked) == False,  # noqa: E712
            col(MealPlan.created_at) < cutoff,
        )
    ).all()

    recorded = 0
    for planned in stale:
        if _existing_signals(db, planned.id):
            continue
        try:
            record(db, planned.id, FeedbackSignal.skipped)
            recorded += 1
        except ValueError:
            continue
    if recorded:
        logger.info("Swept %d skipped suggestion(s)", recorded)
    return recorded


def feedback_for_plan(db: Session, plan_id: int) -> dict[int, dict[str, Any]]:
    """Current explicit rating per planned meal, so the UI can show its state."""
    meals = db.exec(
        select(PlannedMeal.id).where(col(PlannedMeal.plan_id) == plan_id)
    ).all()
    if not meals:
        return {}
    rows = db.exec(
        select(DishFeedback).where(
            col(DishFeedback.planned_meal_id).in_(list(meals)),
            col(DishFeedback.signal).in_([s.value for s in _EXPLICIT]),
        )
    ).all()
    return {
        r.planned_meal_id: {"signal": r.signal.value, "reason": r.reason.value if r.reason else None}
        for r in rows
        if r.planned_meal_id is not None
    }
