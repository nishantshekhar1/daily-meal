"""Feedback capture and preference scoring.

Covers the two properties the loop depends on: a signal is never
double-counted, and a weak inferred signal never overwrites something the user
said directly.
"""
from __future__ import annotations

from app.models import (
    DishFeedback,
    FeedbackReason,
    FeedbackSignal,
    MealAudience,
    PreferenceScope,
)
from app.services import feedback as fb
from app.services import preference as pref

from sqlmodel import select


def _signals(db, planned_meal_id: int) -> list[str]:
    rows = db.exec(
        select(DishFeedback).where(DishFeedback.planned_meal_id == planned_meal_id)
    ).all()
    return sorted(r.signal.value for r in rows)


# ── Recording ────────────────────────────────────────────────────────────────


def test_explicit_rating_is_recorded_with_dish_context(db, make_meal):
    _, dish, planned = make_meal(name="Rajma", cuisine="indian")

    event = fb.record(db, planned.id, FeedbackSignal.thumbs_up)

    assert event.dish_id == dish.id
    assert event.cuisine == "indian"
    assert event.audience == "main"
    assert _signals(db, planned.id) == ["thumbs_up"]


def test_rerating_replaces_rather_than_stacks(db, make_meal):
    _, _, planned = make_meal()

    fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    fb.record(db, planned.id, FeedbackSignal.thumbs_down, FeedbackReason.too_bland)

    assert _signals(db, planned.id) == ["thumbs_down"]
    row = db.exec(
        select(DishFeedback).where(DishFeedback.planned_meal_id == planned.id)
    ).one()
    assert row.reason == FeedbackReason.too_bland


def test_implicit_and_explicit_signals_coexist(db, make_meal):
    """Cooking a dish you also rated is two distinct facts, not a conflict."""
    _, _, planned = make_meal()

    fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    fb.record_cooked(db, planned.id)

    assert _signals(db, planned.id) == ["cooked", "thumbs_up"]


def test_repeated_cook_does_not_double_count(db, make_meal):
    _, _, planned = make_meal()

    fb.record_cooked(db, planned.id)
    fb.record_cooked(db, planned.id)

    assert _signals(db, planned.id) == ["cooked"]


def test_recording_against_unknown_meal_raises(db):
    try:
        fb.record(db, 9999, FeedbackSignal.thumbs_up)
    except ValueError as e:
        assert "not found" in str(e)
    else:
        raise AssertionError("expected ValueError for unknown planned meal")


# ── Reroll ───────────────────────────────────────────────────────────────────


def test_reroll_logs_against_the_last_shown_plan(db, make_meal):
    plan, _, first = make_meal(name="Dal")
    _, _, second = make_meal(name="Paneer", plan=plan)

    recorded = fb.record_reroll(db, "dinner")

    assert recorded == 2
    assert _signals(db, first.id) == ["rerolled"]
    assert _signals(db, second.id) == ["rerolled"]


def test_reroll_leaves_explicitly_rated_meals_alone(db, make_meal):
    """A direct opinion outranks an inferred one."""
    plan, _, rated = make_meal(name="Dal")
    _, _, unrated = make_meal(name="Paneer", plan=plan)
    fb.record(db, rated.id, FeedbackSignal.thumbs_up)

    fb.record_reroll(db, "dinner")

    assert _signals(db, rated.id) == ["thumbs_up"]
    assert _signals(db, unrated.id) == ["rerolled"]


def test_reroll_with_nothing_shown_is_a_noop(db):
    assert fb.record_reroll(db, "dinner") == 0


# ── Skip sweep ───────────────────────────────────────────────────────────────


def test_sweep_marks_old_uncooked_suggestions_as_skipped(db, make_meal, aged_plan):
    _, _, planned = make_meal(plan=aged_plan(hours_ago=48))

    assert fb.sweep_skipped(db) == 1
    assert _signals(db, planned.id) == ["skipped"]


def test_sweep_ignores_recent_suggestions(db, make_meal):
    """An evening plan seen at noon has not been passed over yet."""
    _, _, planned = make_meal()

    assert fb.sweep_skipped(db) == 0
    assert _signals(db, planned.id) == []


def test_sweep_ignores_cooked_meals(db, make_meal, aged_plan):
    _, _, planned = make_meal(plan=aged_plan(hours_ago=48), cooked=True)

    assert fb.sweep_skipped(db) == 0
    assert _signals(db, planned.id) == []


def test_sweep_never_overwrites_existing_feedback(db, make_meal, aged_plan):
    _, _, planned = make_meal(plan=aged_plan(hours_ago=48))
    fb.record(db, planned.id, FeedbackSignal.thumbs_down)

    assert fb.sweep_skipped(db) == 0
    assert _signals(db, planned.id) == ["thumbs_down"]


def test_sweep_is_idempotent(db, make_meal, aged_plan):
    make_meal(plan=aged_plan(hours_ago=48))

    assert fb.sweep_skipped(db) == 1
    assert fb.sweep_skipped(db) == 0


# ── Scoring ──────────────────────────────────────────────────────────────────


def test_no_feedback_scores_exactly_neutral():
    """An unrated scope must never outrank or undercut anything."""
    assert pref.smoothed_score(0, 0) == 0.0


def test_score_is_bounded_and_monotonic():
    scores = [pref.smoothed_score(n, 0) for n in range(0, 20)]
    assert scores == sorted(scores)
    assert all(-1.0 <= s <= 1.0 for s in scores)
    assert pref.smoothed_score(1000, 0) < 1.0


def test_single_signal_moves_score_only_slightly():
    """One bad dinner must not suppress an entire cuisine."""
    assert abs(pref.smoothed_score(0, 1)) < 0.25


def test_balanced_feedback_is_neutral():
    assert pref.smoothed_score(7, 7) == 0.0


# ── Aggregation ──────────────────────────────────────────────────────────────


def test_rebuild_produces_all_three_scopes(db, make_meal):
    _, dish, planned = make_meal(cuisine="indian", ingredients=["chickpeas", "onion"])
    fb.record(db, planned.id, FeedbackSignal.thumbs_up)

    pref.rebuild_profiles(db)

    cuisines = pref.load_scores(db, PreferenceScope.cuisine)
    ingredients = pref.load_scores(db, PreferenceScope.ingredient)
    dishes = pref.load_scores(db, PreferenceScope.dish)

    assert cuisines["indian"] > 0
    assert ingredients["chickpeas"] > 0
    assert ingredients["onion"] > 0
    assert dishes[str(dish.id)] > 0


def test_ingredient_signal_generalizes_across_dishes(db, make_meal):
    """Two disliked dishes sharing an ingredient should convict the ingredient.

    This is the mechanism that makes sparse per-dish feedback usable.
    """
    _, _, a = make_meal(name="Baingan Bharta", ingredients=["eggplant", "onion"])
    _, _, b = make_meal(name="Eggplant Parm", cuisine="italian",
                        ingredients=["eggplant", "cheese"])
    fb.record(db, a.id, FeedbackSignal.thumbs_down)
    fb.record(db, b.id, FeedbackSignal.thumbs_down)

    pref.rebuild_profiles(db)
    ingredients = pref.load_scores(db, PreferenceScope.ingredient)

    assert ingredients["eggplant"] < ingredients["onion"]
    assert ingredients["eggplant"] < 0


def test_staple_ingredients_converge_to_neutral(db, make_meal):
    """An ingredient in both liked and disliked dishes carries no signal.

    This is why no hand-maintained stoplist is needed for salt, oil, onion.
    """
    _, _, liked = make_meal(name="Good", ingredients=["onion", "chickpeas"])
    _, _, disliked = make_meal(name="Bad", ingredients=["onion", "eggplant"])
    fb.record(db, liked.id, FeedbackSignal.thumbs_up)
    fb.record(db, disliked.id, FeedbackSignal.thumbs_down)

    pref.rebuild_profiles(db)
    ingredients = pref.load_scores(db, PreferenceScope.ingredient)

    assert ingredients["onion"] == 0.0
    assert ingredients["chickpeas"] > 0
    assert ingredients["eggplant"] < 0


def test_explicit_signal_outweighs_implicit(db, make_meal):
    _, _, up = make_meal(name="Rated", cuisine="thai")
    _, _, cooked = make_meal(name="Cooked", cuisine="korean")
    fb.record(db, up.id, FeedbackSignal.thumbs_up)
    fb.record_cooked(db, cooked.id)

    pref.rebuild_profiles(db)
    cuisines = pref.load_scores(db, PreferenceScope.cuisine)

    assert cuisines["thai"] > cuisines["korean"] > 0


def test_toddler_and_main_profiles_are_separate(db, make_meal):
    """Adults and toddlers get different dishes, so they get different profiles."""
    _, _, main = make_meal(name="Curry", cuisine="indian", audience=MealAudience.main)
    _, _, tot = make_meal(name="Mash", cuisine="indian", audience=MealAudience.toddler)
    fb.record(db, main.id, FeedbackSignal.thumbs_up)
    fb.record(db, tot.id, FeedbackSignal.thumbs_down)

    pref.rebuild_profiles(db)

    assert pref.load_scores(db, PreferenceScope.cuisine, "main")["indian"] > 0
    assert pref.load_scores(db, PreferenceScope.cuisine, "toddler")["indian"] < 0


def test_rebuild_is_idempotent(db, make_meal):
    _, _, planned = make_meal()
    fb.record(db, planned.id, FeedbackSignal.thumbs_up)

    first = pref.rebuild_profiles(db)
    scores_first = pref.load_scores(db, PreferenceScope.cuisine)
    second = pref.rebuild_profiles(db)
    scores_second = pref.load_scores(db, PreferenceScope.cuisine)

    assert first == second
    assert scores_first == scores_second


def test_rebuild_reflects_retuned_weights(db, make_meal, monkeypatch):
    """Scores are a pure function of the log, so weights can change retroactively."""
    _, _, planned = make_meal()
    fb.record_cooked(db, planned.id)

    pref.rebuild_profiles(db)
    before = pref.load_scores(db, PreferenceScope.cuisine)["indian"]

    monkeypatch.setitem(pref.SIGNAL_WEIGHTS, FeedbackSignal.cooked, 5.0)
    pref.rebuild_profiles(db)
    after = pref.load_scores(db, PreferenceScope.cuisine)["indian"]

    assert after > before


def test_dish_without_recipe_still_scores_cuisine(db, make_meal):
    """A small model may save a dish with no parseable recipe; that must not crash."""
    _, dish, planned = make_meal()
    dish.recipe_json = "{ this is not json"
    db.add(dish)
    db.commit()
    fb.record(db, planned.id, FeedbackSignal.thumbs_up)

    pref.rebuild_profiles(db)

    assert pref.load_scores(db, PreferenceScope.cuisine)["indian"] > 0
    assert pref.load_scores(db, PreferenceScope.ingredient) == {}


# ── Cache invalidation hook ──────────────────────────────────────────────────


def test_profile_version_changes_when_preferences_change(db, make_meal):
    """Without this the prewarm cache would serve pre-feedback plans forever."""
    _, _, planned = make_meal()
    assert pref.profile_version(db) == "none"

    fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)

    assert pref.profile_version(db) != "none"
    assert pref.has_signal(db)
