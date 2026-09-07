"""Score shortlisted dishes against learned household preference.

Pure functions with no database access, so the weighting can be reasoned about
and tested directly. :func:`app.agents.planner_nodes.rank_dishes` loads the
data and calls in here.

The model proposes dishes; this decides their order. Keeping that decision in
arithmetic rather than in a prompt means the ordering is reproducible, and a
household can be shown exactly why a dish ranked where it did.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class RankingWeights:
    """Relative pull of each ranking term.

    The configured cuisine order and learned affinity are deliberately allowed
    to overpower each other depending on evidence. With little feedback the
    learned terms sit near zero (Beta smoothing) and the configured order
    decides everything, which is what a fresh install should do. As feedback
    accumulates the learned terms grow and can override the configured order,
    which is also correct: the config is a preference stated once, feedback is
    what the household actually ate.
    """

    cuisine_priority: float = 1.0
    cuisine_affinity: float = 0.8
    ingredient_affinity: float = 0.6
    dish_affinity: float = 0.5
    recency_penalty: float = 0.7
    # Uncertainty bonus. Without it the loop converges onto the same few
    # well-rated dishes and variety collapses — the most common way a
    # preference loop quietly fails.
    exploration: float = 0.35


DEFAULT_WEIGHTS = RankingWeights()

# Used when ``learn_from_feedback`` is off. Configured cuisine order and
# repetition avoidance survive, since neither is derived from feedback; only
# the learned terms are silenced. This is the baseline to measure the loop's
# effect against.
NO_LEARNING_WEIGHTS = RankingWeights(
    cuisine_affinity=0.0,
    ingredient_affinity=0.0,
    dish_affinity=0.0,
    exploration=0.0,
)

# A dish suggested within this many days is considered repetitive, decaying
# linearly to no penalty at the boundary.
REPETITION_WINDOW_DAYS = 7.0


@dataclass
class DishScore:
    dish: dict[str, Any]
    total: float
    components: dict[str, float] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.dish.get("name", ""))


def cuisine_priority_term(cuisine: Optional[str], priority: list[str]) -> float:
    """1.0 for the most preferred cuisine, 0.0 for one not on the list."""
    if not priority:
        return 0.0
    normalized = str(cuisine or "").strip().lower()
    try:
        rank = priority.index(normalized)
    except ValueError:
        return 0.0
    return (len(priority) - rank) / len(priority)


def ingredient_term(
    ingredients: list[dict[str, Any]], scores: dict[str, float]
) -> float:
    """Mean learned score over the dish's ingredients that carry any signal.

    Averaging only over ingredients with a nonzero score keeps one strongly
    disliked ingredient from being diluted into nothing by a long list of
    neutral staples.
    """
    values = []
    for ing in ingredients or []:
        if not isinstance(ing, dict):
            continue
        score = scores.get(str(ing.get("name", "")).strip().lower(), 0.0)
        if score:
            values.append(score)
    if not values:
        return 0.0
    return sum(values) / len(values)


def recency_term(last_suggested: Optional[datetime], now: datetime) -> float:
    """1.0 for a dish suggested just now, decaying to 0 over the window."""
    if last_suggested is None:
        return 0.0
    days = (now - last_suggested).total_seconds() / 86400.0
    if days >= REPETITION_WINDOW_DAYS:
        return 0.0
    return max(0.0, 1.0 - days / REPETITION_WINDOW_DAYS)


def exploration_term(observations: int) -> float:
    """Higher for dishes we know least about. Never-suggested scores 1.0."""
    return 1.0 / (1.0 + max(0, observations))


def score_dish(
    dish: dict[str, Any],
    *,
    cuisine_priority: list[str],
    cuisine_scores: dict[str, float],
    ingredient_scores: dict[str, float],
    dish_scores: dict[str, float],
    observations: int = 0,
    last_suggested: Optional[datetime] = None,
    now: Optional[datetime] = None,
    weights: RankingWeights = DEFAULT_WEIGHTS,
) -> DishScore:
    now = now or datetime.utcnow()
    cuisine = str(dish.get("cuisine", "") or "").strip().lower()

    components = {
        "cuisine_priority": weights.cuisine_priority
        * cuisine_priority_term(cuisine, cuisine_priority),
        "cuisine_affinity": weights.cuisine_affinity * cuisine_scores.get(cuisine, 0.0),
        "ingredient_affinity": weights.ingredient_affinity
        * ingredient_term(dish.get("ingredients") or [], ingredient_scores),
        "dish_affinity": weights.dish_affinity
        * dish_scores.get(str(dish.get("name", "")).strip().lower(), 0.0),
        "recency_penalty": -weights.recency_penalty
        * recency_term(last_suggested, now),
        "exploration": weights.exploration * exploration_term(observations),
    }
    return DishScore(
        dish=dish,
        total=round(sum(components.values()), 6),
        components={k: round(v, 4) for k, v in components.items()},
    )


def rank(
    dishes: list[dict[str, Any]],
    *,
    cuisine_priority: list[str],
    cuisine_scores: dict[str, float],
    ingredient_scores: dict[str, float],
    dish_scores: dict[str, float],
    observations: Optional[dict[str, int]] = None,
    last_suggested: Optional[dict[str, datetime]] = None,
    now: Optional[datetime] = None,
    weights: RankingWeights = DEFAULT_WEIGHTS,
) -> list[DishScore]:
    """Score and order dishes, best first.

    Ties keep the model's original ordering: Python's sort is stable and the
    model's own sequence is a weak but real quality signal.
    """
    observations = observations or {}
    last_suggested = last_suggested or {}
    now = now or datetime.utcnow()

    scored = [
        score_dish(
            dish,
            cuisine_priority=cuisine_priority,
            cuisine_scores=cuisine_scores,
            ingredient_scores=ingredient_scores,
            dish_scores=dish_scores,
            observations=observations.get(str(dish.get("name", "")).strip().lower(), 0),
            last_suggested=last_suggested.get(str(dish.get("name", "")).strip().lower()),
            now=now,
            weights=weights,
        )
        for dish in dishes
    ]
    return sorted(scored, key=lambda s: -s.total)
