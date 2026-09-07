"""Meal planning and cooking endpoints."""
from __future__ import annotations

import json
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from app.agents.planner import MealPlannerAgent, MealSlot
from app.db.session import engine, get_session
from app.models import HouseholdMember, MealPlan, PlannedMeal, PlanningSession, StockLot, CanonicalIngredient
from app.schemas.meal import (
    AnswerQuestionRequest,
    ConfirmExhaustionRequest,
    CookMealRequest,
    SuggestRequest,
)
from app.core.config import get_settings
from app.services import cook as cook_svc
from app.services import feedback as feedback_svc
from app.services import prewarm, search_budget
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/meals", tags=["meals"])
DB = Annotated[Session, Depends(get_session)]


def _active_members(db: Session) -> list[HouseholdMember]:
    return list(db.exec(select(HouseholdMember).where(HouseholdMember.active == True)).all())


def _cacheable(req: SuggestRequest) -> bool:
    """Only a plain 'what's for lunch' request may be served from the cache.

    Resuming a session replays user answers, and ``force`` is the user asking
    for something other than what they were already shown.
    """
    return req.session_id is None and not req.force


def _log_reroll(db: Session, slot: str) -> None:
    """Asking for a different plan is a rejection of the one already shown.

    Best-effort: a lost feedback row must never stop the user getting a new
    suggestion, which is what they actually asked for.
    """
    try:
        feedback_svc.record_reroll(db, slot)
    except Exception:
        logger.exception("Could not record reroll feedback for %s", slot)


@router.post("/suggest")
async def suggest(req: SuggestRequest, db: DB):
    """Ask the agent for a meal suggestion.

    Returns the prewarmed plan for the slot when one is valid, which is the
    normal case and answers immediately.  Otherwise generates on demand, which
    takes minutes on a local model — prefer ``/suggest/stream`` for that path.

    slot is inferred from the current local time if not provided.
    session_id can be provided to resume a session (after answering questions).
    """
    slot = req.slot or prewarm.current_slot()

    if req.force:
        _log_reroll(db, slot)

    if _cacheable(req):
        cached = prewarm.read_cache(db, slot)
        if cached is not None:
            return cached
        return await prewarm.generate(slot, force=False)

    agent = MealPlannerAgent(db, get_llm_client())
    return await agent.suggest(
        slot=MealSlot(slot),
        members=_active_members(db),
        planning_session_id=req.session_id,
    )


@router.post("/suggest/stream")
async def suggest_stream(req: SuggestRequest):
    """Server-Sent Events variant of :func:`suggest`.

    Planning runs one LLM call per dish, so a full plan takes minutes.  This
    emits ``status`` events as the graph advances and a ``meal`` event per
    finished recipe, then a terminal ``done`` event with the same payload the
    blocking endpoint returns.
    """
    slot = req.slot or prewarm.current_slot()
    session_id = req.session_id
    use_cache = _cacheable(req)

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, default=str)}\n\n"

    async def event_source():
        # Own session rather than the DB dependency: FastAPI tears down
        # yield-dependencies before a streaming body finishes, so the
        # request-scoped session would already be closed in here.
        with Session(engine) as db:
            if req.force:
                _log_reroll(db, slot)

            if use_cache:
                cached = prewarm.read_cache(db, slot)
                if cached is not None:
                    # Nothing to stream — replay the plan and finish.
                    for meal in cached.get("planned_meals", []):
                        yield sse({"type": "meal", "meal": meal})
                    yield sse({"type": "done", "result": cached})
                    return

            agent = MealPlannerAgent(db, get_llm_client())
            try:
                async for event in agent.astream_suggest(
                    slot=MealSlot(slot),
                    members=_active_members(db),
                    planning_session_id=session_id,
                ):
                    if event.get("type") == "done":
                        result = event["result"]
                        if use_cache and result.get("planned_meals"):
                            prewarm.write_cache(db, slot, result)
                    yield sse(event)
            except Exception as e:  # surface as a stream event; headers are long sent
                logger.exception("suggest stream failed")
                yield sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # don't let a proxy buffer the stream
        },
    )


@router.get("/suggest/status")
def suggest_status(db: DB, slot: Optional[str] = None):
    """Whether a prewarmed plan is ready, so the UI can set expectations."""
    resolved = slot or prewarm.current_slot()
    cached = prewarm.read_cache(db, resolved)
    used, limit = search_budget.usage()
    return {
        "slot": resolved,
        "ready": cached is not None,
        "count": len(cached.get("planned_meals", [])) if cached else 0,
        "generated_at": cached.get("generated_at") if cached else None,
        "web_search": {
            "enabled": get_settings().web_search_ready,
            "used_today": used,
            "daily_limit": limit,
        },
    }


@router.post("/answer-question")
def answer_question(req: AnswerQuestionRequest, db: DB):
    """Submit user answer to an ingredient availability question.

    If available=True and quantity/unit provided, creates a stock lot.
    Then the session can be resumed via /suggest with the same session_id.
    """
    ps = db.get(PlanningSession, req.session_id)
    if ps is None:
        raise HTTPException(404, "Session not found")

    import json
    questions = json.loads(ps.questions_json or "[]")
    updated = False
    for q in questions:
        if q["ingredient"].lower() == req.ingredient_name.lower() and q["answer"] is None:
            q["answer"] = req.available
            updated = True
            break

    if not updated:
        raise HTTPException(400, "Question not found in session")

    ps.questions_json = json.dumps(questions)
    db.add(ps)

    if req.available and req.quantity and req.unit:
        # Find or create a stock lot for this ingredient
        from sqlmodel import select
        from datetime import datetime
        from app.models import IngredientAlias
        stmt = select(IngredientAlias).where(
            IngredientAlias.alias == req.ingredient_name.lower()
        )
        alias = db.exec(stmt).first()
        if alias:
            lot = StockLot(
                ingredient_id=alias.ingredient_id,
                quantity=req.quantity,
                unit=req.unit,
                original_quantity=req.quantity,
                source="manual",
                acquired_at=datetime.utcnow(),
            )
            db.add(lot)

    db.commit()
    return {"status": "answered", "session_id": req.session_id}


@router.post("/cook")
def cook_meal(req: CookMealRequest, db: DB):
    """Mark a meal as cooked.  Returns exhaustion candidates for user confirmation."""
    try:
        result = cook_svc.cook_meal(db, req.planned_meal_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Cooking a suggestion is the strongest preference signal the app gets for
    # free. Never let recording it fail the cook itself.
    try:
        feedback_svc.record_cooked(db, req.planned_meal_id)
    except Exception:
        logger.exception("Could not record cooked feedback for meal %d", req.planned_meal_id)

    return result


@router.post("/confirm-exhaustion")
def confirm_exhaustion(req: ConfirmExhaustionRequest, db: DB):
    """User confirms which exhausted lots to remove from stock."""
    try:
        cook_svc.confirm_exhaustion(db, req.cook_event_id, req.remove_lot_ids)
        return {"status": "confirmed"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/plans")
def list_plans(db: DB):
    """List all meal plans (newest first)."""
    plans = db.exec(
        select(MealPlan).where(MealPlan.archived == False).order_by(MealPlan.created_at.desc())
    ).all()
    return plans


@router.get("/plans/{plan_id}")
def get_plan(plan_id: int, db: DB):
    plan = db.get(MealPlan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    meals = db.exec(select(PlannedMeal).where(PlannedMeal.plan_id == plan_id)).all()
    return {"plan": plan, "meals": meals}


@router.get("/graph/mermaid")
def planner_graph_mermaid():
    """Return Mermaid source for the LangGraph meal planner."""
    from app.agents.planner_graph import planner_mermaid

    return {"mermaid": planner_mermaid()}


@router.get("/graph/mermaid.png")
def planner_graph_mermaid_png():
    """Return a PNG rendering of the planner graph."""
    from fastapi.responses import Response

    from app.agents.planner_graph import planner_mermaid_png

    try:
        png = planner_mermaid_png()
    except Exception as e:
        raise HTTPException(
            503,
            f"Could not render Mermaid PNG ({e}). Use GET /meals/graph/mermaid for the source.",
        ) from e
    return Response(content=png, media_type="image/png")
