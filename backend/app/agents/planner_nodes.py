"""LangGraph nodes for meal planning.

LLM proposes dishes/recipes; Python owns inventory math and toddler safety.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from sqlmodel import Session, select

from app.agents import ranking
from app.agents.graph_state import PlannerState
from app.agents.inventory import DishRequirement, InventoryAllocator
from app.agents.safety import validate_toddler_dish
from app.agents.tools import ToolExecutor, search_recipes_web
from app.core.config import get_settings
from app.models import (
    CanonicalIngredient,
    Dish,
    HouseholdMember,
    MealAudience,
    MealPlan,
    MealSlot,
    PlannedMeal,
    PlanningSession,
    StockLot,
)
from app.services import preference
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


_SHORTLIST_SCHEMA = {
    "type": "object",
    "properties": {
        "dishes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "slot": {
                        "type": "string",
                        "enum": ["breakfast", "lunch", "dinner", "snack"],
                    },
                    "audience": {"type": "string", "enum": ["main", "toddler"]},
                    "cuisine": {
                        "type": "string",
                        "description": "Cuisine of the dish, lowercase (e.g. indian, mexican, italian).",
                    },
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
                "required": [
                    "name",
                    "slot",
                    "audience",
                    "cuisine",
                    "description",
                    "ingredients",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["dishes"],
    "additionalProperties": False,
}

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
                "steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "servings",
                "prep_minutes",
                "cook_minutes",
                "ingredients",
                "steps",
            ],
            "additionalProperties": False,
        }
    },
    "required": ["recipe"],
    "additionalProperties": False,
}


def _emit(event: dict[str, Any]) -> None:
    """Push a progress event to a listening stream consumer.

    No-op when the graph is run with plain ``ainvoke`` (no custom stream mode),
    so the same node code serves both the blocking and streaming endpoints.
    """
    try:
        writer = get_stream_writer()
    except Exception:  # not inside a streaming graph run
        return
    if writer is not None:
        writer(event)


def _status(step: str, message: str) -> None:
    _emit({"type": "status", "step": step, "message": message})


def _cfg(config: RunnableConfig) -> tuple[Session, LLMClient]:
    conf = (config or {}).get("configurable") or {}
    db = conf.get("db")
    llm = conf.get("llm")
    if db is None or llm is None:
        raise RuntimeError("Planner graph requires configurable.db and configurable.llm")
    return db, llm


def infer_slot(hour: int) -> MealSlot:
    if 5 <= hour < 11:
        return MealSlot.breakfast
    if 11 <= hour < 15:
        return MealSlot.lunch
    if 15 <= hour < 22:
        return MealSlot.dinner
    return MealSlot.dinner


def household_context(members: list[HouseholdMember]) -> str:
    parts = []
    for m in members:
        age = f", {m.age_months} months old" if m.age_months else ""
        notes = f" ({m.dietary_notes})" if m.dietary_notes else ""
        parts.append(f"{m.name}: {m.kind.value}{age}{notes}")
    return "\n".join(parts)


def pantry_context(db: Session) -> str:
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


async def load_context(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    db, _llm = _cfg(config)
    _status("context", "Reading your pantry and household…")
    slot_name = state.get("slot") or infer_slot(datetime.now().hour).value
    session_id = state.get("session_id") or str(uuid.uuid4())

    ps = db.get(PlanningSession, session_id)
    if ps is None:
        ps = PlanningSession(id=session_id, slot=slot_name)
        db.add(ps)
        db.commit()

    member_ids = state.get("member_ids")
    if member_ids:
        members = list(
            db.exec(
                select(HouseholdMember).where(HouseholdMember.id.in_(member_ids))
            ).all()
        )
    else:
        members = list(
            db.exec(select(HouseholdMember).where(HouseholdMember.active == True)).all()
        )

    toddlers = [m for m in members if m.is_toddler]
    adults = [m for m in members if not m.is_toddler]
    toddler_ages = [m.age_months for m in toddlers if m.age_months is not None]

    return {
        "slot": slot_name,
        "session_id": session_id,
        "member_ids": [m.id for m in members if m.id is not None],
        "pantry_text": pantry_context(db),
        "household_text": household_context(members),
        "total_adults": len(adults),
        "total_toddlers": len(toddlers),
        "has_toddler": bool(toddlers),
        "min_toddler_age": min(toddler_ages) if toddler_ages else None,
        "cuisine_priority": get_settings().cuisine_priority,
        "web_context": "",
        "candidate_dishes": [],
        "feasible_dishes": [],
        "pending_questions": [],
        "safety_reports": [],
        "planned_meals": [],
        "plan_id": None,
        "message": None,
        "error": None,
    }


def _pantry_ingredient_names(pantry_text: str, limit: int) -> list[str]:
    """Pull ingredient names back out of the rendered pantry summary lines."""
    names: list[str] = []
    for line in (pantry_text or "").splitlines():
        stripped = line.strip().lstrip("-").strip()
        if not stripped or stripped.startswith("("):
            continue
        name = stripped.split(":", 1)[0].strip()
        if name:
            names.append(name)
    return names[:limit]


def web_search_query(state: PlannerState) -> str:
    """Build the search query from the slot, top cuisine and what's in stock."""
    cuisines = state.get("cuisine_priority") or []
    parts = [
        cuisines[0] if cuisines else "",
        str(state.get("slot") or "dinner"),
        "recipe",
        *_pantry_ingredient_names(state.get("pantry_text", ""), 5),
    ]
    return " ".join(p for p in parts if p)


async def search_web(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    """Fetch recipe ideas from the web to widen the shortlist.

    A no-op unless ``features.web_search`` is on and the provider is usable
    (Tavily needs an API key), and best-effort even then: any failure leaves
    ``web_context`` empty and planning proceeds on the model's own knowledge.
    """
    settings = get_settings()
    if not settings.web_search_ready:
        return {}

    query = web_search_query(state)
    _status("web_search", f"Searching the web ({settings.web_search_provider})…")
    results = await search_recipes_web(query, limit=settings.web_search_max_results)

    if not results:
        _status("web_search", "No web results — using the model's own knowledge")
        return {"web_context": ""}

    _status("web_search", f"Found {len(results)} web result(s)")
    lines = [
        f"- {r['title']}: {r['snippet']}".rstrip(": ")
        for r in results
        if r.get("title")
    ]
    return {"web_context": "\n".join(lines)}


async def shortlist_dishes(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    _db, llm = _cfg(config)
    slot = state["slot"]
    has_toddler = state.get("has_toddler", False)
    cuisine_priority = state.get("cuisine_priority") or []
    if cuisine_priority:
        cuisine_hint = (
            "The household prefers these cuisines, most preferred first: "
            + ", ".join(cuisine_priority)
            + ". Favour the higher-preference cuisines when the pantry allows, "
            "but do not force a cuisine if the ingredients do not suit it. "
            "Tag every dish with its `cuisine` in lowercase. "
        )
    else:
        cuisine_hint = "Tag every dish with its `cuisine` in lowercase. "

    # Learned preference and repetition avoidance. Both resolve to an empty
    # string until there is real evidence, so a fresh install sends exactly the
    # prompt it always did rather than being nudged by noise.
    audience_scope = "toddler" if has_toddler else "main"
    learned_hint = (
        preference.preference_hint(_db, audience_scope)
        if get_settings().learn_from_feedback
        else ""
    )
    recent = preference.recently_suggested(_db)
    variety_hint = (
        "These were suggested in the last few days, so prefer something different "
        "unless the pantry leaves no good alternative: " + ", ".join(recent) + ". "
        if recent
        else ""
    )

    prompt = [
        {
            "role": "system",
            "content": (
                "You are a household meal planning assistant. Suggest dishes for a "
                f"{slot} meal from what is actually in the pantry. "
                "Do not invent ingredients not listed. "
                f"Household: {state.get('total_adults', 0)} adult(s), "
                f"{state.get('total_toddlers', 0)} toddler(s). "
                + (
                    "Toddlers need separate, age-appropriate versions of dishes "
                    "(softer textures, no honey, no whole nuts, no added salt). "
                    if has_toddler
                    else ""
                )
                + cuisine_hint
                + learned_hint
                + variety_hint
                + "Suggest 3–6 main dishes (audience=main) and, if there are toddlers, "
                "1–3 toddler dishes (audience=toddler). "
                "For each dish list the ingredients with realistic quantities for the "
                "household size. Return valid JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Household members:\n{state.get('household_text', '')}\n\n"
                f"Current pantry:\n{state.get('pantry_text', '')}"
                + (
                    # Inspiration only. Search results routinely mention
                    # ingredients that are not in stock, and the pantry remains
                    # the only source of truth for what can be cooked.
                    "\n\nRecipe ideas found on the web — use them for inspiration "
                    "only, and still only use ingredients from the pantry above:\n"
                    + state["web_context"]
                    if state.get("web_context")
                    else ""
                )
            ),
        },
    ]
    _status("shortlist", f"Thinking up {slot} ideas from your pantry…")
    raw = await llm.chat_json(prompt, _SHORTLIST_SCHEMA)
    dishes = raw.get("dishes", [])
    if not dishes:
        return {"candidate_dishes": [], "error": "no_dishes_shortlisted"}
    _status("shortlist", f"Shortlisted {len(dishes)} dishes")
    return {"candidate_dishes": dishes, "error": None}


def cuisine_rank(cuisine: str | None, priority: list[str]) -> int:
    """Rank index for a cuisine; unlisted or missing cuisines sort last."""
    if not cuisine:
        return len(priority)
    normalized = str(cuisine).strip().lower()
    try:
        return priority.index(normalized)
    except ValueError:
        return len(priority)


async def rank_dishes(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    """Order the shortlist by configured preference and learned feedback.

    Combines the household's configured cuisine order with what they actually
    cooked and rated, a penalty for dishes suggested in the last few days, and
    a bonus for dishes nothing is known about yet. See
    :class:`app.agents.ranking.RankingWeights` for how those trade off.

    Main and toddler dishes are ranked independently, and against separate
    learned profiles, so a toddler dish is never pushed out of the plan by
    better-ranked adult dishes.
    """
    db, _llm = _cfg(config)
    priority = state.get("cuisine_priority") or []
    dishes = list(state.get("candidate_dishes") or [])
    if not dishes:
        return {}

    settings = get_settings()
    learning = settings.learn_from_feedback
    weights = ranking.DEFAULT_WEIGHTS if learning else ranking.NO_LEARNING_WEIGHTS

    def sort_group(group: list[dict[str, Any]], audience: str) -> list[dict[str, Any]]:
        if not group:
            return []
        ctx = preference.build_ranking_context(db, audience)
        ranked = ranking.rank(
            group,
            cuisine_priority=priority,
            cuisine_scores=ctx.cuisine_scores,
            ingredient_scores=ctx.ingredient_scores,
            dish_scores=ctx.dish_scores,
            observations=ctx.observations,
            last_suggested=ctx.last_suggested,
            weights=weights,
        )
        return [scored.dish for scored in ranked]

    learned = learning and preference.has_signal(db)
    if learned:
        _status("rank", "Ranking by your preferences and past feedback…")
    elif priority:
        _status("rank", f"Ranking by cuisine preference ({', '.join(priority)})")
    else:
        _status("rank", "Ranking suggestions…")

    mains = sort_group([d for d in dishes if d.get("audience") != "toddler"], "main")
    toddler = sort_group([d for d in dishes if d.get("audience") == "toddler"], "toddler")
    return {"candidate_dishes": mains + toddler}


async def allocate_inventory(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    db, _llm = _cfg(config)
    if state.get("error"):
        return {}
    _status("allocate", "Checking what your stock actually covers…")
    allocator = InventoryAllocator(db)
    feasible: list[dict] = []
    for dish_data in state.get("candidate_dishes") or []:
        reqs = [
            DishRequirement(
                dish_name=dish_data["name"],
                ingredients=dish_data.get("ingredients", []),
            )
        ]
        result = allocator.allocate(reqs)
        if result.feasible:
            feasible.append(dish_data)
        else:
            logger.info(
                "Dish %r infeasible: missing %s",
                dish_data["name"],
                result.infeasible_ingredients,
            )
    _status("allocate", f"{len(feasible)} dish(es) can be cooked from stock")
    return {"feasible_dishes": feasible}


async def ask_clarifications(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    db, _llm = _cfg(config)
    session_id = state["session_id"]
    ps = db.get(PlanningSession, session_id)
    if ps is None:
        return {
            "pending_questions": [],
            "message": "Not enough pantry stock; planning session missing.",
            "planned_meals": [],
        }

    pending: list[dict] = list(state.get("pending_questions") or [])
    executor = ToolExecutor(db, ps, pending)
    allocator = InventoryAllocator(db)

    for dish_data in (state.get("candidate_dishes") or [])[:3]:
        reqs = [
            DishRequirement(
                dish_name=dish_data["name"],
                ingredients=dish_data.get("ingredients", []),
            )
        ]
        result = allocator.allocate(reqs)
        for missing in result.infeasible_ingredients:
            if ps.budget_exhausted:
                break
            await executor.execute(
                "ask_user_about_ingredient",
                json.dumps(
                    {
                        "ingredient_name": missing,
                        "question": (
                            f"Do you have {missing} at home? "
                            f"It's needed for {dish_data['name']}."
                        ),
                    }
                ),
            )

    # Persist questions so /answer-question can resume the session
    existing = json.loads(ps.questions_json or "[]")
    by_ing = {q.get("ingredient", "").lower(): q for q in existing}
    for q in pending:
        key = str(q.get("ingredient", "")).lower()
        if key and key not in by_ing:
            existing.append(q)
            by_ing[key] = q
    ps.questions_json = json.dumps(existing)
    db.add(ps)
    db.commit()

    return {
        "pending_questions": pending,
        "planned_meals": [],
        "message": (
            "Not enough pantry stock for any suggested dish. "
            "Please answer the questions."
        ),
    }


async def write_recipes(state: PlannerState, config: RunnableConfig) -> dict[str, Any]:
    db, llm = _cfg(config)
    slot = state["slot"]
    session_id = state["session_id"]
    total_servings = state.get("total_adults", 0) + state.get("total_toddlers", 0)
    min_toddler_age = state.get("min_toddler_age")

    planned_meals: list[dict] = []
    safety_reports: list[dict] = []

    plan = MealPlan(week_start=date.today(), session_id=session_id)
    db.add(plan)
    # Commit rather than flush: an open write transaction would hold the SQLite
    # write lock for the whole of the first recipe generation, blocking any
    # other writer (receipt confirm, pantry edit) for ~a minute.
    db.commit()
    plan_id = plan.id

    # Cap the plan: every extra dish is another full LLM generation. The
    # shortlist is already cuisine-ranked, so this keeps the best N.
    queue = (state.get("feasible_dishes") or [])[: get_settings().max_suggestions]
    for index, dish_data in enumerate(queue, start=1):
        audience = MealAudience(dish_data.get("audience", "main"))

        if audience == MealAudience.toddler:
            ing_names = [i["name"] for i in dish_data.get("ingredients", [])]
            report = validate_toddler_dish(
                dish_data["name"], ing_names, min_toddler_age
            )
            safety_reports.append(
                {
                    "dish": dish_data["name"],
                    "safe": report.safe,
                    "violations": [
                        {
                            "ingredient": v.ingredient,
                            "rule": v.rule,
                            "severity": v.severity,
                        }
                        for v in report.violations
                    ],
                }
            )
            if not report.safe:
                logger.warning("Toddler safety violation in dish %r", dish_data["name"])
                _emit({"type": "safety", "report": safety_reports[-1]})
                continue

        _status(
            "recipe",
            f"Writing recipe {index} of {len(queue)}: {dish_data['name']}…",
        )
        recipe_prompt = [
            {
                "role": "system",
                "content": (
                    f"Write a detailed recipe for '{dish_data['name']}' for "
                    f"{total_servings} people "
                    + (f"(audience: {audience.value}). " if audience == MealAudience.toddler else "")
                    + "Use only the listed ingredients and quantities. "
                    + (
                        "This is a TODDLER dish: no added salt, no honey, soft textures, "
                        "chop/grate choking-risk foods appropriately. "
                        if audience == MealAudience.toddler
                        else ""
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
        recipe_raw = await llm.chat_json(recipe_prompt, _RECIPE_SCHEMA)

        cuisine = dish_data.get("cuisine")
        dish = Dish(
            name=dish_data["name"],
            description=dish_data.get("description"),
            recipe_json=json.dumps(recipe_raw.get("recipe", {})),
            suitable_slots=dish_data.get("slot", slot),
            audience=audience,
            cuisine=str(cuisine).strip().lower() if cuisine else None,
            last_suggested_at=datetime.utcnow(),
        )
        db.add(dish)
        db.flush()

        planned = PlannedMeal(
            plan_id=plan_id,
            dish_id=dish.id,
            day=date.today(),
            slot=MealSlot(dish_data.get("slot", slot)),
            audience=audience,
            servings=total_servings,
        )
        db.add(planned)
        db.flush()
        meal = {
            "dish_name": dish.name,
            "dish_id": dish.id,
            "planned_meal_id": planned.id,
            "slot": planned.slot.value,
            "audience": audience.value,
            "cuisine": dish.cuisine,
            "recipe": recipe_raw.get("recipe", {}),
        }
        planned_meals.append(meal)
        # Commit per dish: a streamed card is shown as soon as it is emitted, so
        # the row backing it must already be durable if the client disconnects.
        db.commit()
        _emit({"type": "meal", "meal": meal})

    db.commit()
    return {
        "plan_id": plan_id,
        "planned_meals": planned_meals,
        "safety_reports": safety_reports,
        "pending_questions": [],
        "message": None,
    }


def route_after_shortlist(state: PlannerState) -> str:
    if state.get("error") or not state.get("candidate_dishes"):
        return "empty"
    return "rank"


def route_after_allocate(state: PlannerState) -> str:
    if state.get("feasible_dishes"):
        return "write_recipes"
    if state.get("candidate_dishes"):
        return "ask_clarifications"
    return "empty"
