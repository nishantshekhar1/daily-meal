"""Suggestion feedback: raw signals, learned preferences, and quality metrics.

Three tables with deliberately different lifetimes:

- ``DishFeedback`` is an append-only event log. Nothing is ever aggregated into
  it, so the scoring function can be retuned later and the whole history
  recomputed from scratch.
- ``PreferenceProfile`` is a materialized aggregate, cheap to read during
  ranking. It is derived state and can be rebuilt from ``DishFeedback`` at any
  time.
- ``MetricSnapshot`` is a periodic rollup kept for trend, not for ranking.
"""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, UniqueConstraint


class FeedbackSignal(str, enum.Enum):
    """What the household did, not what it means.

    Interpretation (how much each signal moves a score) lives in
    ``app.services.preference.SIGNAL_WEIGHTS`` so it can be retuned without a
    migration or a rewrite of history.
    """

    thumbs_up = "thumbs_up"          # explicit: user tapped approve
    thumbs_down = "thumbs_down"      # explicit: user tapped reject
    cooked = "cooked"                # implicit: meal was actually cooked
    skipped = "skipped"              # implicit: suggested, never cooked, plan went stale
    rerolled = "rerolled"            # implicit: user asked for a different plan

    @property
    def is_explicit(self) -> bool:
        return self in (FeedbackSignal.thumbs_up, FeedbackSignal.thumbs_down)


class FeedbackReason(str, enum.Enum):
    """Optional structured reason attached to a thumbs-down.

    Free text would be more expressive but cannot be aggregated without another
    LLM call, and this runs on a constrained local box.
    """

    too_bland = "too_bland"
    too_complex = "too_complex"
    disliked_ingredient = "disliked_ingredient"
    too_repetitive = "too_repetitive"
    wrong_portion = "wrong_portion"


class PreferenceScope(str, enum.Enum):
    """Granularity a learned preference applies at.

    A single household produces very sparse data — any given dish is suggested
    once or twice ever — so ``dish`` scope alone would never converge. Most of
    the usable signal is at ``ingredient`` and ``cuisine`` scope, which
    generalize across dishes.
    """

    cuisine = "cuisine"
    ingredient = "ingredient"
    dish = "dish"


class DishFeedback(SQLModel, table=True):
    """One feedback event about one suggested meal.

    Cuisine and audience are denormalized from the dish at write time so that
    aggregation is a single table scan, and so that re-tagging a dish later does
    not silently rewrite past feedback.
    """

    __tablename__ = "dish_feedback"

    id: Optional[int] = Field(default=None, primary_key=True)
    planned_meal_id: Optional[int] = Field(
        default=None, foreign_key="planned_meal.id", index=True
    )
    dish_id: int = Field(foreign_key="dish.id", index=True)

    signal: FeedbackSignal = Field(index=True)
    reason: Optional[FeedbackReason] = Field(default=None)

    audience: str = Field(default="main", index=True)
    cuisine: Optional[str] = Field(default=None, index=True)

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class PreferenceProfile(SQLModel, table=True):
    """Learned affinity for one cuisine, ingredient, or dish.

    Rebuilt wholesale by ``app.services.preference.rebuild_profiles``. Never
    incrementally patched: a full rebuild over a household's data is
    milliseconds, and it keeps the table a pure function of ``DishFeedback``.
    """

    __tablename__ = "preference_profile"
    __table_args__ = (
        UniqueConstraint("scope", "scope_key", "audience", name="uq_preference_scope"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    scope: PreferenceScope = Field(index=True)
    scope_key: str = Field(index=True)
    audience: str = Field(default="main", index=True)

    positive: float = Field(default=0.0, description="Sum of positive signal weights.")
    negative: float = Field(default=0.0, description="Sum of negative signal weights.")
    observations: int = Field(
        default=0,
        description="Raw event count. Drives exploration: low-observation scopes "
        "are uncertain, not disliked.",
    )
    score: float = Field(
        default=0.0,
        description="Beta-smoothed affinity in [-1, 1]. 0 means no usable signal.",
    )

    updated_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class MetricSnapshot(SQLModel, table=True):
    """Periodic rollup of suggestion quality, kept so trend is visible.

    A point-in-time cook-through rate says little; the same number measured
    weekly says whether the feedback loop is working.
    """

    __tablename__ = "metric_snapshot"

    id: Optional[int] = Field(default=None, primary_key=True)
    captured_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    window_days: int = Field(default=30)

    suggestions_count: int = Field(default=0)
    cooked_count: int = Field(default=0)
    distinct_dishes: int = Field(default=0)

    cook_through_rate: float = Field(
        default=0.0, description="Cooked / suggested. The north-star metric."
    )
    thumbs_up_rate: float = Field(
        default=0.0, description="Thumbs up / all explicit ratings. 0 when unrated."
    )
    reroll_rate: float = Field(
        default=0.0, description="Rerolled plans / total plans."
    )
    repetition_rate: float = Field(
        default=0.0,
        description="1 - (distinct dishes / suggestions). High means stale variety.",
    )
