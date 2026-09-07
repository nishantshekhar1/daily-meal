"""The planner nodes' use of learned preference.

Unit tests cover the scoring maths; these cover the wiring, which is where the
mistakes actually live — a ranking function nothing calls, or a prompt hint
built from the wrong audience's profile.
"""
from __future__ import annotations

import asyncio

import pytest

from app.agents import planner_nodes
from app.core.config import get_settings
from app.models import FeedbackSignal, MealAudience
from app.services import feedback as fb
from app.services import preference as pref


class RecordingLLM:
    """Captures the prompt and replies with a fixed shortlist."""

    def __init__(self, dishes):
        self.dishes = dishes
        self.prompts = []

    async def chat_json(self, prompt, schema):
        self.prompts.append(prompt)
        return {"dishes": self.dishes}

    @property
    def system_prompt(self) -> str:
        return self.prompts[-1][0]["content"]


def conf(db, llm=None):
    return {"configurable": {"db": db, "llm": llm or RecordingLLM([])}}


def dish(name, cuisine, audience="main", ingredients=("onion",)):
    return {
        "name": name,
        "cuisine": cuisine,
        "audience": audience,
        "ingredients": [{"name": n, "quantity": 1, "unit": "cup"} for n in ingredients],
    }


@pytest.fixture
def learning(monkeypatch):
    """Force learn_from_feedback on or off for one test."""

    def _set(enabled: bool):
        settings = get_settings()
        features = dict(settings.features)
        features["learn_from_feedback"] = enabled
        monkeypatch.setitem(settings._models_cfg, "features", features)

    return _set


def rank(db, dishes, priority=("indian", "mexican", "italian")):
    state = {"candidate_dishes": list(dishes), "cuisine_priority": list(priority)}
    out = asyncio.run(planner_nodes.rank_dishes(state, conf(db)))
    return [d["name"] for d in out["candidate_dishes"]]


# ── rank_dishes ──────────────────────────────────────────────────────────────


def test_rank_node_falls_back_to_cuisine_order_without_feedback(db):
    assert rank(db, [dish("Pasta", "italian"), dish("Dal", "indian")]) == ["Dal", "Pasta"]


def test_rank_node_applies_learned_preference(db, make_meal, learning):
    learning(True)
    plan = None
    for i in range(6):
        plan, _, planned = make_meal(name=f"Italian {i}", cuisine="italian", plan=plan)
        fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    for i in range(6):
        plan, _, planned = make_meal(name=f"Indian {i}", cuisine="indian", plan=plan)
        fb.record(db, planned.id, FeedbackSignal.thumbs_down)
    pref.rebuild_profiles(db)

    # Configured order puts indian first; sustained feedback should overturn it.
    assert rank(db, [dish("Dal", "indian"), dish("Pasta", "italian")]) == ["Pasta", "Dal"]


def test_rank_node_respects_the_learning_switch(db, make_meal, learning):
    learning(False)
    plan = None
    for i in range(6):
        plan, _, planned = make_meal(name=f"Italian {i}", cuisine="italian", plan=plan)
        fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)

    assert rank(db, [dish("Dal", "indian"), dish("Pasta", "italian")]) == ["Dal", "Pasta"]


def test_rank_node_keeps_toddler_dishes_after_main_dishes(db):
    """A toddler dish must never be pushed out of the plan by adult dishes."""
    dishes = [
        dish("Tot Mash", "italian", audience="toddler"),
        dish("Dal", "indian"),
    ]

    assert rank(db, dishes) == ["Dal", "Tot Mash"]


def test_rank_node_scopes_toddler_dishes_to_the_toddler_profile(db, make_meal, learning):
    """Adults liking a cuisine says nothing about what the toddler will eat."""
    learning(True)
    plan = None
    for i in range(6):
        plan, _, planned = make_meal(
            name=f"Tot {i}", cuisine="italian", audience=MealAudience.toddler, plan=plan
        )
        fb.record(db, planned.id, FeedbackSignal.thumbs_down)
    pref.rebuild_profiles(db)

    ordered = rank(
        db,
        [
            dish("Tot Pasta", "italian", audience="toddler"),
            dish("Tot Dal", "indian", audience="toddler"),
        ],
    )

    assert ordered == ["Tot Dal", "Tot Pasta"]


def test_rank_node_handles_an_empty_shortlist(db):
    out = asyncio.run(
        planner_nodes.rank_dishes({"candidate_dishes": [], "cuisine_priority": []}, conf(db))
    )
    assert out == {}


# ── shortlist prompt ─────────────────────────────────────────────────────────


def shortlist(db, llm, **state):
    base = {
        "slot": "dinner",
        "has_toddler": False,
        "cuisine_priority": ["indian"],
        "total_adults": 2,
        "total_toddlers": 0,
        "household_text": "2 adults",
        "pantry_text": "- rice: 1000 g",
    }
    base.update(state)
    return asyncio.run(planner_nodes.shortlist_dishes(base, conf(db, llm)))


def test_prompt_is_unchanged_before_any_feedback(db, learning):
    """Cold start must not be nudged by a preference nobody expressed."""
    learning(True)
    llm = RecordingLLM([dish("Dal", "indian")])

    shortlist(db, llm)

    assert "tend to like" not in llm.system_prompt
    assert "tend to avoid" not in llm.system_prompt


def test_prompt_carries_learned_preference_once_evidenced(db, make_meal, learning):
    learning(True)
    plan = None
    for i in range(5):
        plan, _, planned = make_meal(
            name=f"Thai {i}", cuisine="thai", ingredients=["chickpeas"], plan=plan
        )
        fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)
    llm = RecordingLLM([dish("Dal", "indian")])

    shortlist(db, llm)

    assert "thai" in llm.system_prompt
    assert "preference, not a rule" in llm.system_prompt


def test_prompt_omits_preference_when_learning_is_off(db, make_meal, learning):
    learning(False)
    plan = None
    for i in range(5):
        plan, _, planned = make_meal(name=f"Thai {i}", cuisine="thai", plan=plan)
        fb.record(db, planned.id, FeedbackSignal.thumbs_up)
    pref.rebuild_profiles(db)
    llm = RecordingLLM([dish("Dal", "indian")])

    shortlist(db, llm)

    assert "tend to like" not in llm.system_prompt


def test_prompt_asks_to_avoid_recently_suggested_dishes(db, make_meal, learning):
    """Dish.last_suggested_at was written from the start and never read."""
    learning(True)
    make_meal(name="Chana Masala")
    llm = RecordingLLM([dish("Dal", "indian")])

    shortlist(db, llm)

    assert "Chana Masala" in llm.system_prompt
    assert "prefer something different" in llm.system_prompt


def test_variety_hint_survives_learning_being_off(db, make_meal, learning):
    """Repetition avoidance is not derived from feedback, so it is not gated."""
    learning(False)
    make_meal(name="Chana Masala")
    llm = RecordingLLM([dish("Dal", "indian")])

    shortlist(db, llm)

    assert "Chana Masala" in llm.system_prompt


def test_empty_shortlist_is_reported_as_an_error(db):
    llm = RecordingLLM([])

    out = shortlist(db, llm)

    assert out["error"] == "no_dishes_shortlisted"
