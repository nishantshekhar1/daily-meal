from __future__ import annotations
from typing import Optional
from pydantic import BaseModel


class SuggestRequest(BaseModel):
    slot: Optional[str] = None          # breakfast | lunch | dinner | snack
    session_id: Optional[str] = None    # resume an existing session


class AnswerQuestionRequest(BaseModel):
    session_id: str
    ingredient_name: str
    available: bool
    quantity: Optional[float] = None
    unit: Optional[str] = None


class CookMealRequest(BaseModel):
    planned_meal_id: int


class ConfirmExhaustionRequest(BaseModel):
    cook_event_id: int
    remove_lot_ids: list[int]
