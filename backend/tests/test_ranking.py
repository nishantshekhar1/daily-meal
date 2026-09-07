"""Preference-aware ranking.

The properties worth protecting are behavioural, not arithmetic: a fresh
install must behave exactly as it did before any of this existed, a little
feedback must not stampede the configured order, a lot of feedback must be
able to override it, and the loop must not collapse onto the same few dishes.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.agents import ranking
from app.agents.ranking import NO_LEARNING_WEIGHTS, RankingWeights
from app.models import FeedbackSignal, MealAudience
from app.services import feedback as fb
from app.services import preference as pref

NOW = datetime(2026, 9, 7, 12, 0, 0)
PRIORITY = ["indian", "mexican", "italian"]


def dish(name, cuisine, ingredients=("onion",)):
    return {
        "name": name,
        "cuisine": cuisine,
        "audience": "main",
        "slot": "dinner",
        "ingredients": [{"name": n, "quantity": 1, "unit": "cup"} for n in ingredients],
    }


def order(scored):
    return [s.name for s in scored]


def rank(dishes, **kw):
    kw.setdefault("cuisine_priority", PRIORITY)
    kw.setdefault("cuisine_scores", {})
    kw.setdefault("ingredient_scores", {})
    kw.setdefault("dish_scores", {})
    kw.setdefault("now", NOW)
    return ranking.rank(dishes, **kw)


# ── Cold start ───────────────────────────────────────────────────────────────


def test_with_no_feedback_configured_cuisine_order_decides():
    """A fresh install must rank exactly as it did before learning existed."""
    dishes = [dish("Pasta", "italian"), dish("Tacos", "mexican"), dish("Dal", "indian")]

    assert order(rank(dishes)) == ["Dal", "Tacos", "Pasta"]


def test_unlisted_cuisine_ranks_last():
    dishes = [dish("Sushi", "japanese"), dish("Dal", "indian")]
    assert order(rank(dishes)) == ["Dal", "Sushi"]


def test_untagged_cuisine_is_treated_as_unlisted():
    dishes = [dish("Mystery", None), dish("Dal", "indian")]
    assert order(rank(dishes)) == ["Dal", "Mystery"]


def test_ties_preserve_the_models_own_order():
    """Stable sort: the model's sequence is a weak but real quality signal."""
    dishes = [dish("First", "indian"), dish("Second", "indian"), dish("Third", "indian")]
    assert order(rank(dishes)) == ["First", "Second", "Third"]


# ── Learned preference ───────────────────────────────────────────────────────


def test_strong_liking_can_override_configured_order():
    """Config is a preference stated once; feedback is what they actually ate."""
    dishes = [dish("Dal", "indian"), dish("Pasta", "italian")]

    ranked = rank(dishes, cuisine_scores={"italian": 0.9, "indian": -0.6})

    assert order(ranked) == ["Pasta", "Dal"]


def test_weak_signal_does_not_overturn_configured_order():
    """One or two ratings must not stampede the household's stated preference."""
    dishes = [dish("Dal", "indian"), dish("Pasta", "italian")]

    # What a single thumbs-up produces after Beta smoothing.
    weak = pref.smoothed_score(1, 0)
    ranked = rank(dishes, cuisine_scores={"italian": weak})

    assert order(ranked) == ["Dal", "Pasta"]


def test_disliked_ingredient_sinks_a_dish_within_its_cuisine():
    dishes = [
        dish("Baingan Bharta", "indian", ingredients=("eggplant", "onion")),
        dish("Chana Masala", "indian", ingredients=("chickpeas", "onion")),
    ]

    ranked = rank(dishes, ingredient_scores={"eggplant": -0.8, "chickpeas": 0.5})

    assert order(ranked) == ["Chana Masala", "Baingan Bharta"]


def test_one_disliked_ingredient_is_not_diluted_by_neutral_staples():
    """Averaging over every ingredient would let a long list bury the signal."""
    loaded = dish("Loaded", "indian", ingredients=("eggplant", "a", "b", "c", "d", "e"))
    plain = dish("Plain", "indian", ingredients=("a", "b"))

    ranked = rank([loaded, plain], ingredient_scores={"eggplant": -0.9})

    assert order(ranked) == ["Plain", "Loaded"]


def test_dish_level_preference_is_applied():
    dishes = [dish("Dal", "indian"), dish("Rajma", "indian")]
    ranked = rank(dishes, dish_scores={"rajma": 0.8})
    assert order(ranked) == ["Rajma", "Dal"]


# ── Repetition and exploration ───────────────────────────────────────────────


def test_recently_suggested_dish_is_penalised():
    dishes = [dish("Dal", "indian"), dish("Rajma", "indian")]

    ranked = rank(dishes, last_suggested={"dal": NOW - timedelta(hours=6)})

    assert order(ranked) == ["Rajma", "Dal"]


def test_repetition_penalty_decays_to_nothing():
    old = ranking.recency_term(NOW - timedelta(days=30), NOW)
    recent = ranking.recency_term(NOW - timedelta(hours=1), NOW)

    assert old == 0.0
    assert recent > 0.9


def test_never_suggested_dish_has_no_penalty():
    assert ranking.recency_term(None, NOW) == 0.0


def test_unknown_dish_is_explored_over_a_well_known_neutral_one():
    """Without this the loop settles on a handful of safe dishes forever."""
    dishes = [dish("Familiar", "indian"), dish("Novel", "indian")]

    ranked = rank(dishes, observations={"familiar": 20})

    assert order(ranked) == ["Novel", "Familiar"]


def test_exploration_does_not_resurrect_a_disliked_dish():
    """Curiosity must not outweigh a clear, well-evidenced rejection."""
    dishes = [dish("Hated", "indian"), dish("Fine", "indian")]

    ranked = rank(
        dishes,
        dish_scores={"hated": -0.9},
        observations={"hated": 0, "fine": 10},
    )

    assert order(ranked) == ["Fine", "Hated"]


def test_exploration_term_shrinks_with_evidence():
    assert ranking.exploration_term(0) == 1.0
    assert ranking.exploration_term(1) == 0.5
    assert ranking.exploration_term(99) < 0.02


# ── Weights ──────────────────────────────────────────────────────────────────


def test_learning_disabled_reproduces_pure_cuisine_order():
    """The baseline configuration, for measuring the loop's effect."""
    dishes = [dish("Pasta", "italian"), dish("Dal", "indian")]

    ranked = rank(
        dishes,
        cuisine_scores={"italian": 0.9, "indian": -0.9},
        dish_scores={"pasta": 0.9},
        observations={"dal": 50},
        weights=NO_LEARNING_WEIGHTS,
    )

    assert order(ranked) == ["Dal", "Pasta"]


def test_learning_disabled_still_avoids_repetition():
    """Repetition avoidance is not derived from feedback, so it survives."""
    dishes = [dish("Dal", "indian"), dish("Rajma", "indian")]

    ranked = rank(
        dishes,
        last_suggested={"dal": NOW - timedelta(hours=2)},
        weights=NO_LEARNING_WEIGHTS,
    )

    assert order(ranked) == ["Rajma", "Dal"]


def test_score_components_are_reported_for_inspection():
    """A ranking a household cannot see is one they cannot argue with."""
    scored = rank([dish("Dal", "indian")], cuisine_scores={"indian": 0.5})[0]

    assert set(scored.components) == {
        "cuisine_priority",
        "cuisine_affinity",
        "ingredient_affinity",
        "dish_affinity",
        "recency_penalty",
        "exploration",
    }
    assert abs(sum(scored.components.values()) - scored.total) < 1e-4


def test_zero_weights_flatten_the_ranking():
    dishes = [dish("A", "indian"), dish("B", "italian")]
    flat = RankingWeights(0, 0, 0, 0, 0, 0)

    ranked = rank(dishes, weights=flat)

    assert all(s.total == 0 for s in ranked)
    assert order(ranked) == ["A", "B"]


# ── Wiring to the store ──────────────────────────────────────────────────────


def test_ranking_context_rekeys_dish_scores_by_name(db, make_meal):
    _, d, planned = make_meal(name="Chana Masala", cuisine="indian",
                              ingredients=["chickpeas"])
    fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)

    ctx = pref.build_ranking_context(db, "main")

    assert ctx.dish_scores["chana masala"] > 0
    assert ctx.cuisine_scores["indian"] > 0
    assert ctx.ingredient_scores["chickpeas"] > 0
    assert ctx.observations["chana masala"] == 1
    assert "chana masala" in ctx.last_suggested


def test_ranking_context_is_scoped_by_audience(db, make_meal):
    _, _, tot = make_meal(name="Mash", cuisine="indian", audience=MealAudience.toddler)
    fb.record(db, tot.id, FeedbackSignal.thumbs_down)
    pref.rebuild_profiles(db)

    assert pref.build_ranking_context(db, "main").cuisine_scores == {}
    assert pref.build_ranking_context(db, "toddler").cuisine_scores["indian"] < 0


def test_recently_suggested_reads_the_field_that_was_never_read(db, make_meal):
    make_meal(name="Dal")
    make_meal(name="Rajma")

    recent = pref.recently_suggested(db, days=3)

    assert set(recent) == {"Dal", "Rajma"}


def test_recently_suggested_ignores_old_dishes(db, make_meal):
    _, dish_row, _ = make_meal(name="Ancient")
    dish_row.last_suggested_at = datetime.utcnow() - timedelta(days=40)
    db.add(dish_row)
    db.commit()

    assert pref.recently_suggested(db, days=3) == []


# ── Prompt hint ──────────────────────────────────────────────────────────────


def test_no_hint_before_there_is_evidence(db, make_meal):
    """Cold start must send the prompt it always sent, not one nudged by noise."""
    _, _, planned = make_meal()
    fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)

    assert pref.preference_hint(db, "main") == ""


def test_hint_appears_once_a_preference_is_well_evidenced(db, make_meal):
    plan = None
    for i in range(5):
        plan_obj, _, planned = make_meal(
            name=f"Dish {i}", cuisine="thai", ingredients=["chickpeas"],
            plan=plan,
        )
        plan = plan_obj
        fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)

    hint = pref.preference_hint(db, "main")

    assert "thai" in hint
    assert "tend to like" in hint


def test_hint_reports_dislikes_too(db, make_meal):
    plan = None
    for i in range(5):
        plan_obj, _, planned = make_meal(
            name=f"Dish {i}", cuisine="thai", ingredients=["eggplant"], plan=plan
        )
        plan = plan_obj
        fb.record(db, planned.id, FeedbackSignal.thumbs_down)
    pref.rebuild_profiles(db)

    hint = pref.preference_hint(db, "main")

    assert "tend to avoid" in hint
    assert "eggplant" in hint
