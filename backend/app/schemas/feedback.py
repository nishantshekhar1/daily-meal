from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# Only ratings a person can give. The implicit signals (cooked, skipped,
# rerolled) are derived by the backend and deliberately not settable over HTTP.
ExplicitSignal = Literal["thumbs_up", "thumbs_down"]

FeedbackReasonValue = Literal[
    "too_bland",
    "too_complex",
    "disliked_ingredient",
    "too_repetitive",
    "wrong_portion",
]


class MealFeedbackRequest(BaseModel):
    planned_meal_id: int
    signal: ExplicitSignal
    reason: Optional[FeedbackReasonValue] = None


class MealFeedbackResponse(BaseModel):
    status: str
    planned_meal_id: int
    signal: str
    reason: Optional[str] = None


class PreferenceEntry(BaseModel):
    scope: str
    scope_key: str
    audience: str
    score: float
    observations: int
    positive: float
    negative: float
