"""The evaluation harness itself.

The harness runs against a real model, which CI has none of, so these tests
drive it with a scripted LLM instead. What is being verified is that the checks
catch what they claim to catch — a harness that silently passes everything is
worse than none, because it manufactures confidence.
"""
from __future__ import annotations

import asyncio

import pytest

from app.eval import checks as C
from app.eval.runner import build_scenario_db, format_comparison, format_report
from app.eval.scenarios import EMPTY_PANTRY, SCENARIOS, STOCKED_OMNIVORE, TODDLER_HOUSEHOLD, by_name


def meal(
    name="Chana Masala",
    cuisine="indian",
    audience="main",
    slot="dinner",
    ingredients=(("canned chickpeas", 400, "g"), ("onion", 100, "g")),
    steps=("Cook.",),
    servings=4,
):
    return {
        "dish_name": name,
        "dish_id": 1,
        "planned_meal_id": 1,
        "slot": slot,
        "audience": audience,
        "cuisine": cuisine,
        "recipe": {
            "servings": servings,
            "prep_minutes": 5,
            "cook_minutes": 15,
            "ingredients": [
                {"name": n, "quantity": q, "unit": u, "preparation": "chopped"}
                for n, q, u in ingredients
            ],
            "steps": list(steps),
        },
    }


@pytest.fixture
def stocked_db():
    session = build_scenario_db(STOCKED_OMNIVORE)
    yield session
    session.close()


@pytest.fixture
def toddler_db():
    session = build_scenario_db(TODDLER_HOUSEHOLD)
    yield session
    session.close()


# ── Fixture database ─────────────────────────────────────────────────────────


def test_scenarios_only_reference_seeded_ingredients():
    """A scenario naming an unseeded ingredient would fail for the wrong reason."""
    for scenario in SCENARIOS:
        session = build_scenario_db(scenario)
        session.close()


def test_scenario_db_is_isolated_per_call():
    a = build_scenario_db(STOCKED_OMNIVORE)
    b = build_scenario_db(EMPTY_PANTRY)
    from sqlmodel import select

    from app.models import StockLot

    assert len(a.exec(select(StockLot)).all()) > 0
    assert len(b.exec(select(StockLot)).all()) == 0
    a.close()
    b.close()


# ── Checks catch what they claim ─────────────────────────────────────────────


def test_completed_fails_on_planner_error():
    assert not C.check_completed({"error": "no_dishes_shortlisted"}).passed
    assert C.check_completed({"planned_meals": []}).passed


def test_min_dishes_enforces_the_scenario_floor():
    assert not C.check_min_dishes({"planned_meals": []}, STOCKED_OMNIVORE).passed
    assert C.check_min_dishes({"planned_meals": []}, EMPTY_PANTRY).passed


def test_ingredients_known_catches_invented_food(stocked_db):
    result = {"planned_meals": [meal(ingredients=(("unobtainium", 1, "g"),))]}

    check = C.check_ingredients_known(result, db=stocked_db)

    assert not check.passed
    assert "unobtainium" in check.detail


def test_ingredients_known_accepts_pantry_aliases(stocked_db):
    """The model says 'chickpeas'; the pantry calls them 'canned chickpeas'."""
    result = {"planned_meals": [meal(ingredients=(("chickpeas", 100, "g"),))]}

    assert C.check_ingredients_known(result, db=stocked_db).passed


def test_ingredients_in_stock_catches_quantity_overreach(stocked_db):
    """Known ingredient, impossible amount."""
    result = {"planned_meals": [meal(ingredients=(("onion", 999999, "g"),))]}

    check = C.check_ingredients_in_stock(result, db=stocked_db)

    assert not check.passed
    assert "onion" in check.detail


def test_ingredients_in_stock_passes_for_a_realistic_plan(stocked_db):
    result = {"planned_meals": [meal()]}
    assert C.check_ingredients_in_stock(result, db=stocked_db).passed


def test_toddler_safety_catches_honey_for_an_infant():
    """Honey under 12 months is the canonical forbidden case."""
    result = {
        "planned_meals": [
            meal(name="Honey Porridge", audience="toddler",
                 ingredients=(("honey", 10, "g"), ("rolled oats", 50, "g")))
        ]
    }

    check = C.check_toddler_safety(result, TODDLER_HOUSEHOLD)

    assert not check.passed
    assert "honey" in check.detail.lower()


def test_toddler_safety_ignores_adult_dishes():
    """Honey is fine for the adults eating alongside the toddler."""
    result = {
        "planned_meals": [
            meal(name="Honey Glaze", audience="main", ingredients=(("honey", 10, "g"),))
        ]
    }

    assert C.check_toddler_safety(result, TODDLER_HOUSEHOLD).passed


def test_toddler_dish_expected_when_household_has_one():
    result = {"planned_meals": [meal(audience="main")]}
    assert not C.check_toddler_dish_present(result, TODDLER_HOUSEHOLD).passed

    result = {"planned_meals": [meal(audience="toddler")]}
    assert C.check_toddler_dish_present(result, TODDLER_HOUSEHOLD).passed


def test_toddler_dish_not_expected_for_adult_household():
    result = {"planned_meals": [meal(audience="main")]}
    assert C.check_toddler_dish_present(result, STOCKED_OMNIVORE).passed


def test_recipes_complete_catches_an_empty_recipe():
    assert not C.check_recipes_complete({"planned_meals": [meal(steps=())]}).passed
    assert not C.check_recipes_complete(
        {"planned_meals": [meal(servings=0)]}
    ).passed
    assert C.check_recipes_complete({"planned_meals": [meal()]}).passed


def test_distinct_dishes_catches_a_repeated_suggestion():
    result = {"planned_meals": [meal(name="Dal"), meal(name="dal")]}
    assert not C.check_distinct_dishes(result).passed


def test_cuisine_tagged_catches_an_unrankable_dish():
    """Ranking keys on the cuisine tag, so an untagged dish cannot be ordered."""
    result = {"planned_meals": [meal(cuisine=None)]}
    assert not C.check_cuisine_tagged(result).passed


def test_slot_appropriate_catches_a_breakfast_at_dinner():
    result = {"planned_meals": [meal(slot="breakfast")]}
    assert not C.check_slot_appropriate(result, STOCKED_OMNIVORE).passed


def test_cuisine_adherence_scores_without_failing():
    result = {"planned_meals": [meal(cuisine="indian"), meal(cuisine="japanese")]}

    check = C.check_cuisine_adherence(result, STOCKED_OMNIVORE)

    assert check.passed  # informative, never fatal
    assert check.score == 0.5


def test_a_crashing_check_is_reported_not_swallowed(stocked_db, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("kaboom")

    boom.__name__ = "check_exploding"
    monkeypatch.setattr(C, "ALL_CHECKS", [boom])

    results = C.run_checks({}, STOCKED_OMNIVORE, stocked_db)

    assert len(results) == 1
    assert not results[0].passed
    assert "kaboom" in results[0].detail


# ── Runner ───────────────────────────────────────────────────────────────────


class ScriptedAgent:
    """Stands in for MealPlannerAgent, returning a canned plan."""

    def __init__(self, result):
        self._result = result

    def __call__(self, *_a, **_kw):
        return self

    async def suggest(self, **_kw):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def run_one(monkeypatch, scenario, result):
    from app.eval import runner

    monkeypatch.setattr(runner, "run_checks", C.run_checks)
    monkeypatch.setattr(
        "app.agents.planner.MealPlannerAgent", ScriptedAgent(result), raising=False
    )
    return asyncio.run(runner.run_scenario(scenario))


def test_runner_scores_a_good_plan(monkeypatch):
    report = run_one(monkeypatch, STOCKED_OMNIVORE, {"planned_meals": [meal(), meal(name="Rajma")]})

    assert report.dish_count == 2
    assert report.passed, [str(c) for c in report.failures]


def test_runner_records_a_planner_crash_instead_of_aborting(monkeypatch):
    """A 4B model breaking the schema must produce a data point, not an exception."""
    report = run_one(monkeypatch, STOCKED_OMNIVORE, RuntimeError("schema broke"))

    assert not report.passed
    assert "schema broke" in (report.error or "")
    assert "completed" in [c.name for c in report.failures]


def test_runner_flags_hallucinated_ingredients(monkeypatch):
    report = run_one(
        monkeypatch,
        STOCKED_OMNIVORE,
        {"planned_meals": [meal(ingredients=(("dragonfruit", 5, "g"),))]},
    )

    assert not report.passed
    assert "ingredients_known" in [c.name for c in report.failures]


def test_empty_pantry_plan_is_an_acceptable_outcome(monkeypatch):
    report = run_one(monkeypatch, EMPTY_PANTRY, {"planned_meals": [], "message": "nothing to cook"})

    assert report.passed


# ── Report formatting ────────────────────────────────────────────────────────


def test_report_renders_failures(monkeypatch):
    from app.eval.runner import SuiteReport

    report = SuiteReport(profile="p.yaml", model="qwen3:4b", started_at="now")
    report.scenarios.append(
        run_one(monkeypatch, STOCKED_OMNIVORE, RuntimeError("boom"))
    )

    text = format_report(report)

    assert "qwen3:4b" in text
    assert "completed" in text
    assert "0/1" in text or "Failures" in text


def test_comparison_table_lines_up_scenarios(monkeypatch):
    from app.eval.runner import SuiteReport

    good = run_one(monkeypatch, STOCKED_OMNIVORE, {"planned_meals": [meal()]})
    bad = run_one(monkeypatch, STOCKED_OMNIVORE, RuntimeError("broke"))

    big = SuiteReport(profile="24gb", model="qwen3:30b-a3b", started_at="now", scenarios=[good])
    small = SuiteReport(profile="8gb", model="qwen3:4b", started_at="now", scenarios=[bad])

    text = format_comparison([big, small])

    assert "qwen3:30b-a3b" in text and "qwen3:4b" in text
    assert "stocked_omnivore" in text
    assert "FAIL" in text


def test_scenario_lookup_rejects_unknown_names():
    with pytest.raises(KeyError):
        by_name("no_such_scenario")
