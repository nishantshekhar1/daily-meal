"""Feedback capture and learned-preference inspection."""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db.session import get_session
from app.models import FeedbackReason, FeedbackSignal, PreferenceProfile, PreferenceScope
from app.schemas.feedback import (
    MealFeedbackRequest,
    MealFeedbackResponse,
    PreferenceEntry,
)
from app.services import feedback as feedback_svc
from app.services import metrics as metrics_svc
from app.services import preference as preference_svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])
DB = Annotated[Session, Depends(get_session)]


@router.post("/meal", response_model=MealFeedbackResponse)
def rate_meal(req: MealFeedbackRequest, db: DB):
    """Record a thumbs up/down on a suggested meal.

    Re-rating the same meal replaces the previous rating rather than stacking,
    so the user can change their mind.
    """
    try:
        event = feedback_svc.record(
            db,
            req.planned_meal_id,
            FeedbackSignal(req.signal),
            FeedbackReason(req.reason) if req.reason else None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    # Rebuild immediately: a household's whole history aggregates in
    # milliseconds, and the user should see the effect on the next suggestion
    # rather than after the next background pass.
    try:
        preference_svc.rebuild_profiles(db)
    except Exception:
        logger.exception("Preference rebuild after rating failed; will retry in background")

    return MealFeedbackResponse(
        status="recorded",
        planned_meal_id=req.planned_meal_id,
        signal=event.signal.value,
        reason=event.reason.value if event.reason else None,
    )


@router.get("/plan/{plan_id}")
def plan_feedback(plan_id: int, db: DB):
    """Existing explicit ratings for a plan, so the UI can render its state."""
    return feedback_svc.feedback_for_plan(db, plan_id)


@router.get("/preferences", response_model=list[PreferenceEntry])
def list_preferences(
    db: DB,
    scope: Optional[str] = Query(None, description="cuisine | ingredient | dish"),
    audience: Optional[str] = Query(None, description="main | toddler"),
    limit: int = Query(50, le=500),
):
    """Inspect what the app has learned.

    Exposed because a preference model the household cannot see is one they
    cannot correct. Sorted by strongest absolute opinion first.
    """
    stmt = select(PreferenceProfile)
    if scope:
        try:
            stmt = stmt.where(PreferenceProfile.scope == PreferenceScope(scope))
        except ValueError as e:
            raise HTTPException(400, f"Unknown scope '{scope}'") from e
    if audience:
        stmt = stmt.where(PreferenceProfile.audience == audience)

    rows = db.exec(stmt).all()
    rows = sorted(rows, key=lambda r: abs(r.score), reverse=True)[:limit]
    return [
        PreferenceEntry(
            scope=r.scope.value if hasattr(r.scope, "value") else str(r.scope),
            scope_key=r.scope_key,
            audience=r.audience,
            score=r.score,
            observations=r.observations,
            positive=r.positive,
            negative=r.negative,
        )
        for r in rows
    ]


@router.post("/rebuild")
def rebuild(db: DB):
    """Recompute all preference profiles from the raw feedback log.

    Needed after retuning ``SIGNAL_WEIGHTS``, since scores are a pure function
    of the log and can be recomputed retroactively.
    """
    count = preference_svc.rebuild_profiles(db)
    return {"status": "rebuilt", "profiles": count}


@router.post("/sweep-skipped")
def sweep_skipped(db: DB):
    """Log suggestions that were passed over. Normally run by the background worker."""
    recorded = feedback_svc.sweep_skipped(db)
    if recorded:
        preference_svc.rebuild_profiles(db)
    return {"status": "swept", "recorded": recorded}


@router.get("/metrics")
def current_metrics(db: DB, window_days: int = Query(30, ge=1, le=365)):
    """Suggestion quality right now, measured live rather than from snapshots."""
    return metrics_svc.compute(db, window_days)


@router.get("/metrics/history")
def metrics_history(db: DB, limit: int = Query(30, le=365)):
    """Past snapshots, newest first. A single value says little; the trend does."""
    return metrics_svc.history(db, limit)


@router.post("/metrics/snapshot")
def capture_metrics(db: DB, window_days: int = Query(30, ge=1, le=365)):
    """Record a snapshot. Normally run by the background worker."""
    return metrics_svc.snapshot(db, window_days)
