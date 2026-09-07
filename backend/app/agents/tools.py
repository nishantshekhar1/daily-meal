"""Tool definitions for the meal planning agent.

Each tool is defined in two parts:
  - A JSON schema (for the LLM's tool parameter)
  - A Python implementation (executed by the agent loop after the model calls it)

The ClarificationBudget is enforced here — the code, not the prompt, prevents
the model from asking more than 3 questions about non-stocked ingredients.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import CanonicalIngredient, PlanningSession, StockLot

logger = logging.getLogger(__name__)


# ── Tool schemas (sent to the LLM as `tools` parameter) ─────────────────────────

ASK_INGREDIENT_TOOL = {
    "type": "function",
    "function": {
        "name": "ask_user_about_ingredient",
        "description": (
            "Ask the user whether a specific ingredient is available, "
            "when it is not already in the pantry stock. "
            "Limited to 3 questions per planning session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ingredient_name": {
                    "type": "string",
                    "description": "Canonical ingredient name to ask about.",
                },
                "question": {
                    "type": "string",
                    "description": "Natural-language question to display to the user.",
                },
            },
            "required": ["ingredient_name", "question"],
        },
    },
}

SEARCH_RECIPES_TOOL = {
    "type": "function",
    "function": {
        "name": "search_recipes",
        "description": (
            "Search the web for recipe ideas using available ingredients. "
            "Only call this when web search is enabled."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'quick dinner chicken spinach rice'.",
                }
            },
            "required": ["query"],
        },
    },
}

GET_PANTRY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_pantry_summary",
        "description": "Return the current pantry stock as a JSON summary.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


def get_active_tools(web_search_enabled: bool) -> list[dict]:
    tools = [ASK_INGREDIENT_TOOL, GET_PANTRY_TOOL]
    if web_search_enabled:
        tools.append(SEARCH_RECIPES_TOOL)
    return tools


# ── Tool implementations ─────────────────────────────────────────────────────

class ToolExecutor:
    """Executes tool calls from the agent loop.

    Holds references to the DB session and the planning session so it can
    enforce state-based constraints (e.g. clarification budget).
    """

    def __init__(
        self,
        db: Session,
        planning_session: PlanningSession,
        pending_questions: list[dict],  # mutated in-place by ask_user_about_ingredient
    ) -> None:
        self._db = db
        self._session = planning_session
        self._questions = pending_questions
        self._settings = get_settings()

    async def execute(self, tool_name: str, arguments_json: str) -> str:
        """Dispatch a tool call.  Returns a JSON string to feed back to the model."""
        try:
            args = json.loads(arguments_json)
        except json.JSONDecodeError:
            return json.dumps({"error": "invalid_json_arguments"})

        if tool_name == "ask_user_about_ingredient":
            return await self._ask_ingredient(args)
        if tool_name == "search_recipes":
            return await self._search_recipes(args)
        if tool_name == "get_pantry_summary":
            return await self._get_pantry()
        return json.dumps({"error": f"unknown tool: {tool_name}"})

    async def _ask_ingredient(self, args: dict) -> str:
        ing_name: str = args.get("ingredient_name", "").strip()
        question: str = args.get("question", "").strip()

        # Guard 1: already in stock → don't ask, return stock info
        in_stock = self._check_stock(ing_name)
        if in_stock is not None:
            return json.dumps({"status": "already_in_stock", "quantity": in_stock})

        # Guard 2: budget exhausted
        if self._session.budget_exhausted:
            return json.dumps({
                "status": "budget_exhausted",
                "message": "No more clarification questions allowed this session.",
            })

        # Guard 3: already asked about this ingredient
        asked = {q["ingredient"] for q in self._questions}
        if ing_name.lower() in asked:
            return json.dumps({"status": "already_asked", "ingredient": ing_name})

        # Enqueue the question — the API layer surfaces it to the frontend
        self._questions.append({"ingredient": ing_name, "question": question, "answer": None})
        self._session.questions_asked += 1
        self._db.add(self._session)
        self._db.commit()

        return json.dumps({"status": "question_queued", "ingredient": ing_name})

    def _check_stock(self, ingredient_name: str) -> Optional[str]:
        """Return a display quantity string if ingredient is in stock, else None."""
        stmt = (
            select(StockLot)
            .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
            .where(CanonicalIngredient.name == ingredient_name)
            .where(StockLot.quantity > 0)
        )
        lots = self._db.exec(stmt).all()
        if not lots:
            return None
        total = sum(l.quantity for l in lots)
        unit = lots[0].unit
        return f"{total:.1f} {unit}"

    async def _search_recipes(self, args: dict) -> str:
        if not self._settings.web_search_enabled:
            return json.dumps({"error": "web_search_disabled"})
        query: str = args.get("query", "")
        url = self._settings.searxng_url.rstrip("/") + "/search"
        timeout = self._settings.features.get("web_search_timeout_s", 8)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    url,
                    params={"q": query, "format": "json", "categories": "general"},
                )
                resp.raise_for_status()
                data = resp.json()
                results = [
                    {"title": r.get("title"), "url": r.get("url"), "snippet": r.get("content", "")[:300]}
                    for r in data.get("results", [])[:5]
                ]
                return json.dumps({"results": results})
        except Exception as exc:
            logger.warning("SearXNG search failed: %s", exc)
            return json.dumps({"error": str(exc)})

    async def _get_pantry(self) -> str:
        stmt = (
            select(StockLot, CanonicalIngredient)
            .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
            .where(StockLot.quantity > 0)
        )
        rows = self._db.exec(stmt).all()
        summary: dict[str, dict] = {}
        for lot, ing in rows:
            key = ing.name
            if key not in summary:
                summary[key] = {"quantity": 0.0, "unit": lot.unit, "category": ing.category}
            summary[key]["quantity"] += lot.quantity
        return json.dumps(summary)
