"""Turn raw feedback signals into learned preference scores.

Scoring is deliberately plain arithmetic rather than a model: it runs in
milliseconds on a household's worth of data, is inspectable in the DB, and
cannot invent a preference the user never expressed. This keeps the app's
"LLM proposes, Python decides" split intact — the model suggests dishes, this
module decides how to order them.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from app.models import (
    Dish,
    DishFeedback,
    FeedbackSignal,
    PreferenceProfile,
    PreferenceScope,
)

logger = logging.getLogger(__name__)

# How much each signal moves a score, and in which direction.
#
# Explicit ratings outweigh implicit ones because implicit signals are
# confounded: an uncooked dish may simply have been a lunch idea seen at
# bedtime. Negative implicit weights are deliberately small — absence of
# action is weak evidence of dislike.
SIGNAL_WEIGHTS: dict[FeedbackSignal, float] = {
    FeedbackSignal.thumbs_up: 1.0,
    FeedbackSignal.thumbs_down: -1.0,
    FeedbackSignal.cooked: 0.6,
    FeedbackSignal.skipped: -0.25,
    FeedbackSignal.rerolled: -0.15,
}

# Beta prior. PRIOR_WEIGHT is expressed in units of observations: with a weight
# of 4, a scope needs roughly four events before its score moves decisively off
# neutral. This is what stops one bad dinner from suppressing a whole cuisine.
PRIOR_RATE = 0.5
PRIOR_WEIGHT = 4.0

# Below this many observations a scope is treated as unknown rather than
# disliked, which matters for the exploration slot in ranking.
CONFIDENT_OBSERVATIONS = 3


def smoothed_score(positive: float, negative: float) -> float:
    """Beta-smoothed affinity in [-1, 1]; 0 means no usable signal.

    Uses a symmetric prior so that a scope with no feedback scores exactly 0
    and therefore never outranks or undercuts anything on its own.
    """
    total = positive + negative
    if total <= 0:
        return 0.0
    rate = (positive + PRIOR_WEIGHT * PRIOR_RATE) / (total + PRIOR_WEIGHT)
    return round(2.0 * rate - 1.0, 6)


def dish_ingredient_names(dish: Dish) -> list[str]:
    """Lowercased ingredient names from a dish's stored recipe.

    Returns an empty list rather than raising when the recipe is missing or
    malformed — a dish saved by a small model may have neither.
    """
    if not dish.recipe_json:
        return []
    try:
        recipe = json.loads(dish.recipe_json)
    except (ValueError, TypeError):
        return []
    if isinstance(recipe, dict) and "recipe" in recipe:
        recipe = recipe["recipe"]
    if not isinstance(recipe, dict):
        return []
    names = []
    for ing in recipe.get("ingredients") or []:
        name = (ing or {}).get("name") if isinstance(ing, dict) else None
        if name:
            names.append(str(name).strip().lower())
    return names


def _accumulate(
    buckets: dict[tuple[PreferenceScope, str, str], list[float]],
    scope: PreferenceScope,
    key: Optional[str],
    audience: str,
    weight: float,
) -> None:
    if not key:
        return
    buckets.setdefault((scope, key, audience), [0.0, 0.0, 0.0])
    bucket = buckets[(scope, key, audience)]
    if weight >= 0:
        bucket[0] += weight
    else:
        bucket[1] += abs(weight)
    bucket[2] += 1


def rebuild_profiles(db: Session) -> int:
    """Recompute every PreferenceProfile row from the DishFeedback log.

    A full rebuild rather than an incremental update: the whole history of a
    single household is small, and this keeps the aggregate a pure function of
    the event log, so ``SIGNAL_WEIGHTS`` can be retuned and applied
    retroactively without a migration.

    Returns the number of profile rows written.
    """
    events = list(db.exec(select(DishFeedback)).all())

    # dish_id -> ingredient names, resolved once per dish rather than per event
    dish_ids = {e.dish_id for e in events}
    ingredients_by_dish: dict[int, list[str]] = {}
    if dish_ids:
        for dish in db.exec(select(Dish).where(Dish.id.in_(dish_ids))).all():
            ingredients_by_dish[dish.id] = dish_ingredient_names(dish)

    # (scope, key, audience) -> [positive, negative, observations]
    buckets: dict[tuple[PreferenceScope, str, str], list[float]] = {}

    for event in events:
        weight = SIGNAL_WEIGHTS.get(event.signal, 0.0)
        if weight == 0.0:
            continue
        audience = event.audience or "main"

        _accumulate(buckets, PreferenceScope.cuisine, event.cuisine, audience, weight)
        _accumulate(
            buckets, PreferenceScope.dish, str(event.dish_id), audience, weight
        )
        # Ingredient scope is where sparse per-dish feedback generalizes. Staples
        # that appear in both liked and disliked dishes converge toward 0 on
        # their own, so no hand-maintained stoplist is needed.
        for name in ingredients_by_dish.get(event.dish_id, []):
            _accumulate(buckets, PreferenceScope.ingredient, name, audience, weight)

    db.exec(PreferenceProfile.__table__.delete())

    now = datetime.utcnow()
    for (scope, key, audience), (positive, negative, observations) in buckets.items():
        db.add(
            PreferenceProfile(
                scope=scope,
                scope_key=key,
                audience=audience,
                positive=round(positive, 6),
                negative=round(negative, 6),
                observations=int(observations),
                score=smoothed_score(positive, negative),
                updated_at=now,
            )
        )
    db.commit()
    logger.info("Rebuilt %d preference profiles from %d events", len(buckets), len(events))
    return len(buckets)


def load_scores(
    db: Session, scope: PreferenceScope, audience: str = "main"
) -> dict[str, float]:
    """Map of scope_key -> score for one scope and audience."""
    rows = db.exec(
        select(PreferenceProfile).where(
            PreferenceProfile.scope == scope,
            PreferenceProfile.audience == audience,
        )
    ).all()
    return {r.scope_key: r.score for r in rows}


def load_observations(
    db: Session, scope: PreferenceScope, audience: str = "main"
) -> dict[str, int]:
    """Map of scope_key -> observation count, for confidence checks."""
    rows = db.exec(
        select(PreferenceProfile).where(
            PreferenceProfile.scope == scope,
            PreferenceProfile.audience == audience,
        )
    ).all()
    return {r.scope_key: r.observations for r in rows}


@dataclass
class RankingContext:
    """Everything :mod:`app.agents.ranking` needs, keyed the way it wants it.

    Learned dish scores are stored against dish ids but the shortlist only has
    names, so they are re-keyed by name here rather than in the ranker, which
    stays free of database concerns.
    """

    cuisine_scores: dict[str, float] = field(default_factory=dict)
    ingredient_scores: dict[str, float] = field(default_factory=dict)
    dish_scores: dict[str, float] = field(default_factory=dict)
    observations: dict[str, int] = field(default_factory=dict)
    last_suggested: dict[str, datetime] = field(default_factory=dict)


def build_ranking_context(db: Session, audience: str = "main") -> RankingContext:
    """Load learned preference plus repetition history for one audience."""
    dish_scores_by_id = load_scores(db, PreferenceScope.dish, audience)
    dish_obs_by_id = load_observations(db, PreferenceScope.dish, audience)

    dish_scores: dict[str, float] = {}
    observations: dict[str, int] = {}
    last_suggested: dict[str, datetime] = {}

    for dish in db.exec(select(Dish)).all():
        key = (dish.name or "").strip().lower()
        if not key:
            continue
        dish_id = str(dish.id)
        if dish_id in dish_scores_by_id:
            dish_scores[key] = dish_scores_by_id[dish_id]
        if dish_id in dish_obs_by_id:
            observations[key] = dish_obs_by_id[dish_id]
        if dish.last_suggested_at is not None:
            # A dish can exist several times under the same name; the most
            # recent suggestion is what drives repetition fatigue.
            prior = last_suggested.get(key)
            if prior is None or dish.last_suggested_at > prior:
                last_suggested[key] = dish.last_suggested_at

    return RankingContext(
        cuisine_scores=load_scores(db, PreferenceScope.cuisine, audience),
        ingredient_scores=load_scores(db, PreferenceScope.ingredient, audience),
        dish_scores=dish_scores,
        observations=observations,
        last_suggested=last_suggested,
    )


# Only opinions this strong, backed by this much evidence, are worth spending
# prompt tokens on. A weak signal in the prompt is noise the model may latch
# onto far harder than the score itself warrants.
HINT_SCORE_THRESHOLD = 0.15
HINT_MAX_ITEMS = 4


def _hint_items(scores: dict[str, float], observations: dict[str, int], positive: bool):
    picked = [
        (key, score)
        for key, score in scores.items()
        if (score > HINT_SCORE_THRESHOLD if positive else score < -HINT_SCORE_THRESHOLD)
        and observations.get(key, 0) >= CONFIDENT_OBSERVATIONS
    ]
    picked.sort(key=lambda kv: -abs(kv[1]))
    return [key for key, _ in picked[:HINT_MAX_ITEMS]]


def preference_hint(db: Session, audience: str = "main") -> str:
    """A short natural-language summary of learned preference for the prompt.

    Returns an empty string when there is not enough evidence, which keeps the
    cold-start prompt byte-identical to what it was before this feature and
    avoids nudging a small model with noise.
    """
    cuisine_scores = load_scores(db, PreferenceScope.cuisine, audience)
    cuisine_obs = load_observations(db, PreferenceScope.cuisine, audience)
    ingredient_scores = load_scores(db, PreferenceScope.ingredient, audience)
    ingredient_obs = load_observations(db, PreferenceScope.ingredient, audience)

    liked = _hint_items(cuisine_scores, cuisine_obs, True) + _hint_items(
        ingredient_scores, ingredient_obs, True
    )
    disliked = _hint_items(cuisine_scores, cuisine_obs, False) + _hint_items(
        ingredient_scores, ingredient_obs, False
    )
    if not liked and not disliked:
        return ""

    parts = ["Based on what this household actually cooked and rated before: "]
    if liked:
        parts.append("they tend to like " + ", ".join(liked) + ". ")
    if disliked:
        parts.append("they tend to avoid " + ", ".join(disliked) + ". ")
    parts.append(
        "Treat this as a preference, not a rule — the pantry still decides what "
        "is possible. "
    )
    return "".join(parts)


def recently_suggested(db: Session, days: int = 3, limit: int = 12) -> list[str]:
    """Dish names put in front of the household lately, to avoid repeating them.

    ``Dish.last_suggested_at`` has been written on every suggestion since the
    beginning and was never read; this is the first thing to use it.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = db.exec(
        select(Dish)
        .where(Dish.last_suggested_at != None)  # noqa: E711
        .where(Dish.last_suggested_at >= cutoff)
        .order_by(Dish.last_suggested_at.desc())
        .limit(limit)
    ).all()
    seen: list[str] = []
    for dish in rows:
        name = (dish.name or "").strip()
        if name and name.lower() not in {s.lower() for s in seen}:
            seen.append(name)
    return seen


def profile_version(db: Session) -> str:
    """Version token that changes whenever learned preference scores change.

    Folded into the prewarm cache fingerprint. Without it a freshly learned
    preference would not reach the user until the pantry happened to change.

    Hashes the scores themselves rather than ``updated_at``. A timestamp is
    cheaper but wrong in both directions: ``rebuild_profiles`` stamps every row
    with one ``utcnow()``, so two rebuilds inside the same second are
    indistinguishable and the cache survives feedback it should have been
    invalidated by; and a scheduled rebuild that changes nothing would discard
    a perfectly good plan. Content hashing is correct by construction.
    """
    rows = db.exec(
        select(
            PreferenceProfile.scope,
            PreferenceProfile.scope_key,
            PreferenceProfile.audience,
            PreferenceProfile.score,
        )
    ).all()
    if not rows:
        return "none"
    payload = sorted(
        (
            scope.value if hasattr(scope, "value") else str(scope),
            str(key),
            str(audience),
            round(float(score), 6),
        )
        for scope, key, audience, score in rows
    )
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode())
    return digest.hexdigest()[:16]


def has_signal(db: Session) -> bool:
    """Whether any learned preference exists yet, for cold-start branching."""
    return db.exec(select(PreferenceProfile.id).limit(1)).first() is not None
