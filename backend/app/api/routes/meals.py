"""Meal planning and cooking endpoints."""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.agents.planner import MealPlannerAgent, MealSlot
from app.db.session import get_session
from app.models import HouseholdMember, MealPlan, PlannedMeal, PlanningSession, StockLot, CanonicalIngredient
from app.schemas.meal import (
    AnswerQuestionRequest,
    ConfirmExhaustionRequest,
    CookMealRequest,
    SuggestRequest,
)
from app.services import cook as cook_svc
from app.services.llm_client import get_llm_client

router = APIRouter(prefix="/meals", tags=["meals"])
DB = Annotated[Session, Depends(get_session)]


@router.post("/suggest")
async def suggest(req: SuggestRequest, db: DB):
    """Ask the agent for a meal suggestion.

    slot is inferred from current time if not provided.
    session_id can be provided to resume a session (after answering questions).
    """
    llm = get_llm_client()
    agent = MealPlannerAgent(db, llm)

    slot = MealSlot(req.slot) if req.slot else None
    members = list(db.exec(select(HouseholdMember).where(HouseholdMember.active == True)).all())

    result = await agent.suggest(
        slot=slot,
        members=members,
        planning_session_id=req.session_id,
    )
    return result


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
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))


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
