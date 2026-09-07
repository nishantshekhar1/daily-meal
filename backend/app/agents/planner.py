"""Meal planning agent — the main AI orchestration layer.

Three-stage planning:
  Stage 1: Shortlist candidate dishes (what could we make from this pantry?)
  Stage 2: Allocate to slots (does stock cover the whole plan?)
  Stage 3: Write full recipes for the winning set

The LLM drives stages 1 and 3 via tool-calling; Stage 2 is deterministic Python.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from app.agents.inventory import DishRequirement, InventoryAllocator
from app.agents.safety import SafetyReport, validate_toddler_dish
from app.agents.tools import ToolExecutor, get_active_tools
from app.core.config import get_settings
from app.models import (
    Dish,
    HouseholdMember,
    MealAudience,
    MealPlan,
    MealSlot,
    MemberKind,
    PlannedMeal,
    PlanningSession,
    StockLot,
    CanonicalIngredient,
)
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

# JSON schema for the Stage-1 dish shortlist
_SHORTLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "dishes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slot": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
                    "audience": {"type": "string", "enum": ["main", "toddler"]},
                    "description": {"type": "string"},
                    "ingredients": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit": {"type": "string"},
                            },
                            "required": ["name", "quantity", "unit"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "slot", "audience", "description", "ingredients"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["dishes"],
    "additionalProperties": False,
}

# JSON schema for Stage-3 full recipe
_RECIPE_SCHEMA = {
    "type": "object",
    "properties": {
        "recipe": {
            "type": "object",
            "properties": {
                "servings": {"type": "integer"},
                "prep_minutes": {"type": "integer"},
                "cook_minutes": {"type": "integer"},
                "ingredients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "number"},
                            "unit": {"type": "string"},
                            "preparation": {"type": "string"},
                        },
                        "required": ["name", "quantity", "unit", "preparation"],
                        "additionalProperties": False,
                    },
                },
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["servings", "prep_minutes", "cook_minutes", "ingredients", "steps"],
            "additionalProperties": False,
        }
    },
    "required": ["recipe"],
    "additionalProperties": False,
}


def _infer_slot(hour: int) -> MealSlot:
    if 5 <= hour < 11:
        return MealSlot.breakfast
    if 11 <= hour < 15:
        return MealSlot.lunch
    if 15 <= hour < 22:
        return MealSlot.dinner
    return MealSlot.dinner


def _household_context(members: list[HouseholdMember]) -> str:
    parts = []
    for m in members:
        age = f", {m.age_months} months old" if m.age_months else ""
        notes = f" ({m.dietary_notes})" if m.dietary_notes else ""
        parts.append(f"{m.name}: {m.kind.value}{age}{notes}")
    return "\n".join(parts)


def _pantry_context(db: Session) -> str:
    from sqlmodel import select
    stmt = (
        select(StockLot, CanonicalIngredient)
        .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
        .where(StockLot.quantity > 0)
    )
    rows = db.exec(stmt).all()
    summary: dict[str, dict] = {}
    for lot, ing in rows:
        if ing.name not in summary:
            summary[ing.name] = {"qty": 0.0, "unit": lot.unit, "category": ing.category}
        summary[ing.name]["qty"] += lot.quantity
    lines = [
        f"- {name}: {v['qty']:.1f} {v['unit']} ({v['category']})"
        for name, v in summary.items()
    ]
    return "\n".join(lines) if lines else "(pantry is empty)"


class MealPlannerAgent:
    """Orchestrates multi-stage meal planning with local LLM tool calling."""

    MAX_AGENT_ITERATIONS = 12
    MAX_SHORTLIST_DISHES = 10

    def __init__(self, db: Session, llm: LLMClient) -> None:
        self._db = db
        self._llm = llm
        self._settings = get_settings()

    async def suggest(
        self,
        slot: Optional[MealSlot] = None,
        members: Optional[list[HouseholdMember]] = None,
        planning_session_id: Optional[str] = None,
    ) -> dict:
        """Entry point for a meal suggestion request.

        Returns a dict with keys: plan_id, planned_meals, pending_questions, safety_reports.
        """
        if members is None:
            from sqlmodel import select
            members = list(self._db.exec(
                select(HouseholdMember).where(HouseholdMember.active == True)
            ).all())

        if slot is None:
            slot = _infer_slot(datetime.utcnow().hour)

        # Load or create the planning session
        session_id = planning_session_id or str(uuid.uuid4())
        ps = self._db.get(PlanningSession, session_id)
        if ps is None:
            ps = PlanningSession(id=session_id, slot=slot.value)
            self._db.add(ps)
            self._db.commit()

        pending_questions: list[dict] = []
        tool_executor = ToolExecutor(self._db, ps, pending_questions)

        # ── Stage 1: Shortlist ────────────────────────────────────────────────
        toddlers = [m for m in members if m.is_toddler]
        adults = [m for m in members if not m.is_toddler]
        has_toddler = bool(toddlers)

        pantry = _pantry_context(self._db)
        household = _household_context(members)
        total_adults = len(adults)
        total_toddlers = len(toddlers)

        shortlist_prompt = [
            {
                "role": "system",
                "content": (
                    "You are a household meal planning assistant. Suggest dishes for a "
                    f"{slot.value} meal from what is actually in the pantry. "
                    "Do not invent ingredients not listed. "
                    f"Household: {total_adults} adult(s), {total_toddlers} toddler(s). "
                    + (
                        "Toddlers need separate, age-appropriate versions of dishes "
                        "(softer textures, no honey, no whole nuts, no added salt). "
                        if has_toddler else ""
                    )
                    + "Suggest 3–6 main dishes (audience=main) and, if there are toddlers, "
                    "1–3 toddler dishes (audience=toddler). "
                    "For each dish list the ingredients with realistic quantities for the household size. "
                    "Return valid JSON only."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Household members:\n{household}\n\n"
                    f"Current pantry:\n{pantry}"
                ),
            },
        ]

        shortlist_raw = await self._llm.chat_json(shortlist_prompt, _SHORTLIST_SCHEMA)
        candidate_dishes: list[dict] = shortlist_raw.get("dishes", [])

        if not candidate_dishes:
            return {"error": "no_dishes_shortlisted", "session_id": session_id}

        # ── Stage 2: Inventory allocation ─────────────────────────────────────
        allocator = InventoryAllocator(self._db)
        feasible_dishes = []
        for dish_data in candidate_dishes:
            reqs = [
                DishRequirement(
                    dish_name=dish_data["name"],
                    ingredients=dish_data.get("ingredients", []),
                )
            ]
            result = allocator.allocate(reqs)
            if result.feasible:
                feasible_dishes.append(dish_data)
            else:
                logger.info(
                    "Dish %r infeasible: missing %s",
                    dish_data["name"],
                    result.infeasible_ingredients,
                )

        if not feasible_dishes:
            # Attempt to ask about missing ingredients (within budget)
            # Re-allocate to identify what's missing and queue questions
            for dish_data in candidate_dishes[:3]:
                reqs = [DishRequirement(dish_name=dish_data["name"], ingredients=dish_data.get("ingredients", []))]
                result = allocator.allocate(reqs)
                for missing in result.infeasible_ingredients:
                    if not ps.budget_exhausted:
                        await tool_executor.execute(
                            "ask_user_about_ingredient",
                            json.dumps({
                                "ingredient_name": missing,
                                "question": f"Do you have {missing} at home? It's needed for {dish_data['name']}.",
                            }),
                        )

            return {
                "session_id": session_id,
                "pending_questions": pending_questions,
                "planned_meals": [],
                "message": "Not enough pantry stock for any suggested dish. Please answer the questions.",
            }

        # ── Stage 3: Full recipes for winning dishes ───────────────────────────
        toddler_ages = [m.age_months for m in toddlers if m.age_months is not None]
        min_toddler_age = min(toddler_ages) if toddler_ages else None
        total_servings = total_adults + total_toddlers

        planned_meals = []
        safety_reports: list[dict] = []

        # Persist the plan
        from datetime import date
        plan = MealPlan(week_start=date.today(), session_id=session_id)
        self._db.add(plan)
        self._db.flush()

        for dish_data in feasible_dishes[:self.MAX_SHORTLIST_DISHES]:
            audience = MealAudience(dish_data.get("audience", "main"))

            # Toddler safety check before writing recipe
            if audience == MealAudience.toddler:
                ing_names = [i["name"] for i in dish_data.get("ingredients", [])]
                report = validate_toddler_dish(dish_data["name"], ing_names, min_toddler_age)
                safety_reports.append({
                    "dish": dish_data["name"],
                    "safe": report.safe,
                    "violations": [
                        {"ingredient": v.ingredient, "rule": v.rule, "severity": v.severity}
                        for v in report.violations
                    ],
                })
                if not report.safe:
                    logger.warning("Toddler safety violation in dish %r", dish_data["name"])
                    continue  # Drop unsafe dish; don't generate recipe

            # Generate full recipe
            recipe_prompt = [
                {
                    "role": "system",
                    "content": (
                        f"Write a detailed recipe for '{dish_data['name']}' for {total_servings} people "
                        + (f"(audience: {audience.value}). " if audience == MealAudience.toddler else "")
                        + "Use only the listed ingredients and quantities. "
                        + (
                            "This is a TODDLER dish: no added salt, no honey, soft textures, "
                            "chop/grate choking-risk foods appropriately. "
                            if audience == MealAudience.toddler else ""
                        )
                        + "Return JSON matching the schema."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Dish: {dish_data['name']}\n"
                        f"Description: {dish_data.get('description', '')}\n"
                        f"Ingredients:\n"
                        + "\n".join(
                            f"- {i['name']}: {i['quantity']} {i['unit']}"
                            for i in dish_data.get("ingredients", [])
                        )
                    ),
                },
            ]
            recipe_raw = await self._llm.chat_json(recipe_prompt, _RECIPE_SCHEMA)

            # Persist dish
            dish = Dish(
                name=dish_data["name"],
                description=dish_data.get("description"),
                recipe_json=json.dumps(recipe_raw.get("recipe", {})),
                suitable_slots=dish_data.get("slot", slot.value),
                audience=audience,
                last_suggested_at=datetime.utcnow(),
            )
            self._db.add(dish)
            self._db.flush()

            planned = PlannedMeal(
                plan_id=plan.id,
                dish_id=dish.id,
                day=date.today(),
                slot=MealSlot(dish_data.get("slot", slot.value)),
                audience=audience,
                servings=total_servings,
            )
            self._db.add(planned)
            planned_meals.append({
                "dish_name": dish.name,
                "dish_id": dish.id,
                "slot": planned.slot.value,
                "audience": audience.value,
                "recipe": recipe_raw.get("recipe", {}),
            })

        self._db.commit()

        return {
            "session_id": session_id,
            "plan_id": plan.id,
            "planned_meals": planned_meals,
            "pending_questions": pending_questions,
            "safety_reports": safety_reports,
        }
