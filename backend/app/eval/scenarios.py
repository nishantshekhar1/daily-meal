"""Fixture households and pantries for offline evaluation.

Ingredient names are drawn from the canonical seed set (``app.db.seed``) so
that resolution and unit conversion are exercised for real rather than being
stubbed away. A scenario that referenced an unseeded ingredient would fail for
the wrong reason.

Scenarios are chosen to cover the ways planning goes wrong, not the happy path
alone: an empty pantry, a pantry too sparse for a full plan, and a household
whose toddler makes several stocked items unsafe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models import MemberKind


@dataclass(frozen=True)
class StockSpec:
    ingredient: str
    quantity: float
    unit: str


@dataclass(frozen=True)
class MemberSpec:
    name: str
    kind: MemberKind
    age_months: Optional[int] = None


@dataclass(frozen=True)
class Scenario:
    """One planning situation and what must hold for any plan produced from it."""

    name: str
    description: str
    slot: str
    members: list[MemberSpec]
    stock: list[StockSpec]

    # A plan with fewer dishes than this is a failure. Zero means an empty plan
    # is an acceptable outcome — the pantry genuinely cannot support a meal.
    min_dishes: int = 1
    # Whether the household should receive at least one toddler-audience dish.
    expect_toddler_dish: bool = False
    # Cuisines that could plausibly be cooked from this pantry, for adherence
    # scoring. Empty disables the check.
    plausible_cuisines: list[str] = field(default_factory=list)


_ADULTS = [
    MemberSpec("Adult A", MemberKind.adult_male),
    MemberSpec("Adult B", MemberKind.adult_female),
]


STOCKED_OMNIVORE = Scenario(
    name="stocked_omnivore",
    description="Two adults, a well-stocked pantry. The ordinary case.",
    slot="dinner",
    members=_ADULTS,
    stock=[
        StockSpec("rice", 2000, "g"),
        StockSpec("pasta", 1000, "g"),
        StockSpec("chicken breast", 900, "g"),
        StockSpec("ground beef", 500, "g"),
        StockSpec("canned chickpeas", 800, "g"),
        StockSpec("red lentils", 500, "g"),
        StockSpec("onion", 600, "g"),
        StockSpec("garlic", 10, "count"),
        StockSpec("tomatoes", 700, "g"),
        StockSpec("canned diced tomatoes", 800, "g"),
        StockSpec("spinach", 300, "g"),
        StockSpec("carrots", 400, "g"),
        StockSpec("potatoes", 1200, "g"),
        StockSpec("frozen peas", 400, "g"),
        StockSpec("cheddar cheese", 300, "g"),
        StockSpec("butter", 250, "g"),
        StockSpec("olive oil", 500, "mL"),
        StockSpec("salt", 500, "g"),
        StockSpec("eggs", 12, "count"),
    ],
    min_dishes=2,
    plausible_cuisines=["indian", "italian", "mexican"],
)


TODDLER_HOUSEHOLD = Scenario(
    name="toddler_household",
    description=(
        "Two adults and a 10-month-old, with honey, whole almonds and grapes "
        "in stock. Every one of those is age-gated or a choking risk, so the "
        "safety rules must actually bite."
    ),
    slot="lunch",
    members=[*_ADULTS, MemberSpec("Toddler", MemberKind.toddler, age_months=10)],
    stock=[
        StockSpec("rice", 1000, "g"),
        StockSpec("red lentils", 500, "g"),
        StockSpec("carrots", 500, "g"),
        StockSpec("potatoes", 800, "g"),
        StockSpec("spinach", 200, "g"),
        StockSpec("whole milk", 1000, "mL"),
        StockSpec("butter", 200, "g"),
        StockSpec("eggs", 12, "count"),
        StockSpec("honey", 300, "g"),
        StockSpec("almonds", 200, "g"),
        StockSpec("grapes", 400, "g"),
        StockSpec("olive oil", 300, "mL"),
        StockSpec("salt", 400, "g"),
    ],
    min_dishes=1,
    expect_toddler_dish=True,
    plausible_cuisines=["indian", "italian"],
)


SPARSE_PANTRY = Scenario(
    name="sparse_pantry",
    description=(
        "Barely enough for one meal. The interesting failure is a model that "
        "invents ingredients to pad the plan out."
    ),
    slot="dinner",
    members=_ADULTS,
    stock=[
        StockSpec("eggs", 6, "count"),
        StockSpec("rice", 400, "g"),
        StockSpec("onion", 150, "g"),
        StockSpec("olive oil", 100, "mL"),
        StockSpec("salt", 200, "g"),
    ],
    min_dishes=1,
)


BREAKFAST_ONLY = Scenario(
    name="breakfast_only",
    description="Breakfast staples at breakfast. Checks slot-appropriate suggestions.",
    slot="breakfast",
    members=_ADULTS,
    stock=[
        StockSpec("rolled oats", 800, "g"),
        StockSpec("whole milk", 2000, "mL"),
        StockSpec("eggs", 12, "count"),
        StockSpec("butter", 200, "g"),
        StockSpec("honey", 300, "g"),
        StockSpec("orange juice", 1000, "mL"),
        StockSpec("all-purpose flour", 1000, "g"),
        StockSpec("sugar", 500, "g"),
    ],
    min_dishes=1,
)


EMPTY_PANTRY = Scenario(
    name="empty_pantry",
    description=(
        "Nothing in stock. Must degrade to a clarification or an empty plan, "
        "never to a crash or to a recipe built from imaginary food."
    ),
    slot="dinner",
    members=_ADULTS,
    stock=[],
    min_dishes=0,
)


SCENARIOS: list[Scenario] = [
    STOCKED_OMNIVORE,
    TODDLER_HOUSEHOLD,
    SPARSE_PANTRY,
    BREAKFAST_ONLY,
    EMPTY_PANTRY,
]


def by_name(name: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.name == name:
            return scenario
    raise KeyError(f"Unknown scenario '{name}'. Known: {[s.name for s in SCENARIOS]}")
