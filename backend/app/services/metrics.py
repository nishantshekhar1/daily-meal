"""Quality metrics for the suggestion loop, measured from live usage.

The offline harness (:mod:`app.eval`) answers "is the model capable". These
answer "is the household actually eating this", which is the only question
that finally matters and the only one a fixture cannot fake.

Cook-through rate is the north star: of the meals we suggested, how many got
made. Everything else is diagnostic — a healthy cook-through rate with a
climbing repetition rate means the loop has collapsed onto a few safe dishes
and needs more exploration.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlmodel import Session, col, select

from app.models import (
    DishFeedback,
    FeedbackSignal,
    MealPlan,
    MetricSnapshot,
    PlannedMeal,
)

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_DAYS = 30


def _ratio(numerator: float, denominator: float) -> float:
    """Zero when undefined, so an unused app reads as 0 rather than crashing."""
    if not denominator:
        return 0.0
    return round(numerator / denominator, 4)


def compute(db: Session, window_days: int = DEFAULT_WINDOW_DAYS) -> dict[str, Any]:
    """Measure suggestion quality over the trailing ``window_days``."""
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    suggestions = list(
        db.exec(
            select(PlannedMeal)
            .join(MealPlan, col(MealPlan.id) == col(PlannedMeal.plan_id))
            .where(col(MealPlan.created_at) >= cutoff)
        ).all()
    )
    suggestions_count = len(suggestions)
    cooked_count = sum(1 for s in suggestions if s.cooked)
    distinct_dishes = len({s.dish_id for s in suggestions})

    plan_ids = {s.plan_id for s in suggestions}
    feedback = list(
        db.exec(select(DishFeedback).where(col(DishFeedback.created_at) >= cutoff)).all()
    )
    thumbs_up = sum(1 for f in feedback if f.signal == FeedbackSignal.thumbs_up)
    thumbs_down = sum(1 for f in feedback if f.signal == FeedbackSignal.thumbs_down)

    rerolled_meal_ids = {
        f.planned_meal_id for f in feedback if f.signal == FeedbackSignal.rerolled
    }
    rerolled_plans = {
        s.plan_id for s in suggestions if s.id in rerolled_meal_ids
    }

    return {
        "window_days": window_days,
        "suggestions_count": suggestions_count,
        "cooked_count": cooked_count,
        "distinct_dishes": distinct_dishes,
        "cook_through_rate": _ratio(cooked_count, suggestions_count),
        "thumbs_up_rate": _ratio(thumbs_up, thumbs_up + thumbs_down),
        "reroll_rate": _ratio(len(rerolled_plans), len(plan_ids)),
        # Distinct dishes over suggestions, inverted: 0 means every suggestion
        # was a different dish, high means the same few keep coming back.
        "repetition_rate": round(1.0 - _ratio(distinct_dishes, suggestions_count), 4)
        if suggestions_count
        else 0.0,
        "explicit_ratings": thumbs_up + thumbs_down,
    }


def snapshot(db: Session, window_days: int = DEFAULT_WINDOW_DAYS) -> MetricSnapshot:
    """Persist a measurement so the trend survives, not just the current value."""
    values = compute(db, window_days)
    row = MetricSnapshot(
        captured_at=datetime.utcnow(),
        window_days=values["window_days"],
        suggestions_count=values["suggestions_count"],
        cooked_count=values["cooked_count"],
        distinct_dishes=values["distinct_dishes"],
        cook_through_rate=values["cook_through_rate"],
        thumbs_up_rate=values["thumbs_up_rate"],
        reroll_rate=values["reroll_rate"],
        repetition_rate=values["repetition_rate"],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(
        "Metrics: cook-through %.0f%% over %d suggestion(s), repetition %.0f%%",
        row.cook_through_rate * 100,
        row.suggestions_count,
        row.repetition_rate * 100,
    )
    return row


def history(db: Session, limit: int = 30) -> list[MetricSnapshot]:
    """Most recent snapshots, newest first."""
    return list(
        db.exec(
            select(MetricSnapshot)
            .order_by(col(MetricSnapshot.captured_at).desc())
            .limit(limit)
        ).all()
    )


def latest(db: Session) -> Optional[MetricSnapshot]:
    rows = history(db, limit=1)
    return rows[0] if rows else None
