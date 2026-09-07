"""Structural checks on a generated plan.

Every check here is decidable without human judgement, which is the point: it
makes "did quality drop" a measurement rather than an argument. Subjective
qualities like variety or appeal are deliberately out of scope.

A check returning ``False`` means the model produced something the application
should not have accepted. Several of these re-verify invariants the planner is
already supposed to enforce — that redundancy is what catches a regression in
the planner itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlmodel import Session, select

from app.agents.inventory import DishRequirement, InventoryAllocator
from app.agents.safety import validate_toddler_dish
from app.eval.scenarios import Scenario
from app.models import CanonicalIngredient, IngredientAlias


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    # Optional 0..1 measurement for checks that are informative even when they
    # pass, e.g. how closely the plan followed cuisine preferences.
    score: Optional[float] = None

    def __str__(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{mark} {self.name}{suffix}"


def _meals(result: dict[str, Any]) -> list[dict[str, Any]]:
    return result.get("planned_meals") or []


def _recipe_ingredients(meal: dict[str, Any]) -> list[dict[str, Any]]:
    recipe = meal.get("recipe") or {}
    return [i for i in (recipe.get("ingredients") or []) if isinstance(i, dict)]


def _resolve(db: Session, name: str) -> bool:
    """Whether an ingredient name maps to something the pantry knows about."""
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    if db.exec(select(IngredientAlias).where(IngredientAlias.alias == lowered)).first():
        return True
    return (
        db.exec(
            select(CanonicalIngredient).where(CanonicalIngredient.name == lowered)
        ).first()
        is not None
    )


# ── Individual checks ────────────────────────────────────────────────────────


def check_completed(result: dict[str, Any], **_) -> CheckResult:
    """The planner returned a result rather than an error.

    On the smallest profiles this is the check that fails: a 4B model breaks
    the nested shortlist schema and the graph has no repair path.
    """
    error = result.get("error")
    return CheckResult("completed", not error, str(error or ""))


def check_min_dishes(result: dict[str, Any], scenario: Scenario, **_) -> CheckResult:
    count = len(_meals(result))
    ok = count >= scenario.min_dishes
    return CheckResult(
        "min_dishes", ok, f"{count} dish(es), wanted >= {scenario.min_dishes}"
    )


def check_recipes_complete(result: dict[str, Any], **_) -> CheckResult:
    """Every dish carries a usable recipe, not an empty shell."""
    bad: list[str] = []
    for meal in _meals(result):
        recipe = meal.get("recipe") or {}
        name = meal.get("dish_name", "?")
        if not recipe.get("steps"):
            bad.append(f"{name}: no steps")
        elif not _recipe_ingredients(meal):
            bad.append(f"{name}: no ingredients")
        elif not recipe.get("servings"):
            bad.append(f"{name}: no servings")
    return CheckResult("recipes_complete", not bad, "; ".join(bad))


def check_ingredients_known(result: dict[str, Any], db: Session, **_) -> CheckResult:
    """No invented food.

    The strongest signal of a model operating beyond its capacity: ingredients
    that do not exist in the pantry vocabulary at all.
    """
    unknown: set[str] = set()
    for meal in _meals(result):
        for ing in _recipe_ingredients(meal):
            if not _resolve(db, ing.get("name", "")):
                unknown.add(str(ing.get("name")))
    return CheckResult(
        "ingredients_known",
        not unknown,
        f"unknown: {sorted(unknown)}" if unknown else "",
    )


def check_ingredients_in_stock(result: dict[str, Any], db: Session, **_) -> CheckResult:
    """The whole plan is actually cookable from stock, re-checked from scratch.

    Redundant with the planner's own allocation step by design — if this ever
    fails, a dish reached the user without passing InventoryAllocator.
    """
    meals = _meals(result)
    if not meals:
        return CheckResult("ingredients_in_stock", True, "no dishes")

    requirements = [
        DishRequirement(
            dish_name=m.get("dish_name", "?"),
            ingredients=[
                {
                    "name": i.get("name", ""),
                    "quantity": float(i.get("quantity") or 0),
                    "unit": i.get("unit", ""),
                }
                for i in _recipe_ingredients(m)
            ],
        )
        for m in meals
    ]
    allocation = InventoryAllocator(db).allocate(requirements)
    return CheckResult(
        "ingredients_in_stock",
        allocation.feasible,
        f"short: {sorted(set(allocation.infeasible_ingredients))}"
        if not allocation.feasible
        else "",
    )


def check_toddler_safety(
    result: dict[str, Any], scenario: Scenario, **_
) -> CheckResult:
    """No toddler dish violates an age-gated rule.

    This must hold at every model size, because the rules are enforced in
    Python. A failure here is an application bug, not a weak model.
    """
    ages = [m.age_months for m in scenario.members if m.age_months is not None]
    youngest = min(ages) if ages else None

    violations: list[str] = []
    for meal in _meals(result):
        if meal.get("audience") != "toddler":
            continue
        names = [str(i.get("name", "")) for i in _recipe_ingredients(meal)]
        report = validate_toddler_dish(meal.get("dish_name", "?"), names, youngest)
        if not report.safe:
            forbidden = [
                v.ingredient for v in report.violations if v.severity == "forbidden"
            ]
            violations.append(f"{meal.get('dish_name')}: {forbidden}")
    return CheckResult("toddler_safety", not violations, "; ".join(violations))


def check_toddler_dish_present(
    result: dict[str, Any], scenario: Scenario, **_
) -> CheckResult:
    if not scenario.expect_toddler_dish:
        return CheckResult("toddler_dish_present", True, "not applicable")
    has = any(m.get("audience") == "toddler" for m in _meals(result))
    return CheckResult("toddler_dish_present", has, "" if has else "no toddler dish")


def check_distinct_dishes(result: dict[str, Any], **_) -> CheckResult:
    names = [str(m.get("dish_name", "")).strip().lower() for m in _meals(result)]
    duplicates = {n for n in names if names.count(n) > 1}
    return CheckResult(
        "distinct_dishes", not duplicates, f"repeated: {sorted(duplicates)}" if duplicates else ""
    )


def check_cuisine_tagged(result: dict[str, Any], **_) -> CheckResult:
    """Ranking is driven by the cuisine tag, so an untagged dish cannot be ranked."""
    untagged = [m.get("dish_name") for m in _meals(result) if not m.get("cuisine")]
    return CheckResult(
        "cuisine_tagged", not untagged, f"untagged: {untagged}" if untagged else ""
    )


def check_slot_appropriate(
    result: dict[str, Any], scenario: Scenario, **_
) -> CheckResult:
    wrong = [
        f"{m.get('dish_name')}={m.get('slot')}"
        for m in _meals(result)
        if m.get("slot") and m.get("slot") != scenario.slot
    ]
    return CheckResult("slot_appropriate", not wrong, "; ".join(wrong))


def check_cuisine_adherence(
    result: dict[str, Any], scenario: Scenario, **_
) -> CheckResult:
    """How much of the plan lands in a cuisine the pantry could plausibly support.

    Scored rather than pass/fail below a floor: a pantry sometimes genuinely
    suits nothing on the preference list, and forcing one would be worse.
    """
    meals = _meals(result)
    if not scenario.plausible_cuisines or not meals:
        return CheckResult("cuisine_adherence", True, "not applicable", None)
    wanted = {c.lower() for c in scenario.plausible_cuisines}
    hits = sum(1 for m in meals if str(m.get("cuisine", "")).lower() in wanted)
    ratio = hits / len(meals)
    return CheckResult(
        "cuisine_adherence",
        True,
        f"{hits}/{len(meals)} in {sorted(wanted)}",
        round(ratio, 3),
    )


ALL_CHECKS = [
    check_completed,
    check_min_dishes,
    check_recipes_complete,
    check_ingredients_known,
    check_ingredients_in_stock,
    check_toddler_safety,
    check_toddler_dish_present,
    check_distinct_dishes,
    check_cuisine_tagged,
    check_slot_appropriate,
    check_cuisine_adherence,
]


def run_checks(
    result: dict[str, Any], scenario: Scenario, db: Session
) -> list[CheckResult]:
    """Run every check, isolating failures so one exception does not hide the rest."""
    results: list[CheckResult] = []
    for check in ALL_CHECKS:
        try:
            results.append(check(result=result, scenario=scenario, db=db))
        except Exception as e:  # a check crashing is itself a finding
            results.append(
                CheckResult(check.__name__.removeprefix("check_"), False, f"raised {e!r}")
            )
    return results
