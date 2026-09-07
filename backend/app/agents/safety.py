"""Toddler safety validator — hard-coded CDC/NHS rules.

This module is deterministic. It never delegates safety decisions to the LLM.
Rules are sourced from:
  - CDC: https://www.cdc.gov/infant-toddler-nutrition/foods-and-drinks/foods-and-drinks-to-avoid-or-limit.html
  - NHS: https://www.nhs.uk/baby/weaning-and-feeding/foods-to-avoid-giving-babies-and-young-children/
  - INSPQ (Quebec Public Health): choking risk foods
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Ingredient names (lowercase) that are forbidden / require modification for toddlers.
# format: name → (min_age_months_to_allow, reason)
FORBIDDEN_UNDER: dict[str, tuple[int, str]] = {
    "honey": (12, "Risk of infant botulism (CDC/NHS). No honey under 12 months."),
    "raw honey": (12, "Risk of infant botulism."),
    "manuka honey": (12, "Risk of infant botulism."),
}

# Whole nuts (choking risk) forbidden under 5 years (60 months)
WHOLE_NUT_NAMES = {
    "almonds", "whole almonds", "raw almonds",
    "walnuts", "whole walnuts",
    "cashews", "whole cashews",
    "hazelnuts",
    "pecans",
    "macadamia nuts",
    "peanuts",  # whole; peanut butter is fine
    "pine nuts",
}

# High-choking-risk foods that require preparation notes
CHOKING_RISK: dict[str, str] = {
    "grapes": "Cut lengthways into quarters for toddlers.",
    "red grapes": "Cut lengthways into quarters for toddlers.",
    "green grapes": "Cut lengthways into quarters for toddlers.",
    "cherry tomatoes": "Cut lengthways into quarters for toddlers.",
    "tomatoes": "If using cherry tomatoes, quarter them lengthways for toddlers.",
    "carrots": "Grate raw carrots for toddlers; or cook until soft.",
    "raw carrots": "Grate for toddlers; do not serve as whole sticks.",
    "celery": "Finely chop; do not serve as sticks for toddlers.",
    "raw celery": "Finely chop; do not serve as sticks for toddlers.",
    "popcorn": "Do not serve to children under 4 years.",
    "hard candy": "Do not serve to toddlers.",
    "jelly cubes": "Raw jelly cubes are a choking hazard.",
}

# High-sodium ingredients — flag when in toddler dish
HIGH_SODIUM = {
    "salt", "sea salt", "kosher salt", "table salt",
    "soy sauce", "light soy sauce", "dark soy sauce", "tamari",
    "fish sauce",
    "worcestershire sauce",
    "stock cubes", "bouillon",
    "bacon", "ham", "sausage", "hot dog",
}


@dataclass
class SafetyViolation:
    ingredient: str
    rule: str
    severity: str  # "forbidden" | "warning" | "preparation_note"
    preparation_note: Optional[str] = None


@dataclass
class SafetyReport:
    dish_name: str
    violations: list[SafetyViolation] = field(default_factory=list)
    safe: bool = True

    def add(self, v: SafetyViolation) -> None:
        self.violations.append(v)
        if v.severity == "forbidden":
            self.safe = False


def validate_toddler_dish(
    dish_name: str,
    ingredients: list[str],
    toddler_age_months: Optional[int] = None,
) -> SafetyReport:
    """Check a list of ingredient names against hard-coded toddler safety rules.

    Args:
        dish_name:           Name of the dish being checked.
        ingredients:         List of ingredient name strings from the recipe.
        toddler_age_months:  Youngest toddler's age. If None, assumes most
                             restrictive (treat as newborn / < 12 months).
    Returns:
        SafetyReport with all violations and a `safe` flag.
    """
    report = SafetyReport(dish_name=dish_name)
    age = toddler_age_months if toddler_age_months is not None else 0

    for raw_name in ingredients:
        name = raw_name.lower().strip()

        # 1. Age-gated forbidden ingredients
        for forbidden_name, (min_age, reason) in FORBIDDEN_UNDER.items():
            if name == forbidden_name and age < min_age:
                report.add(SafetyViolation(
                    ingredient=raw_name,
                    rule=reason,
                    severity="forbidden",
                ))

        # 2. Whole nuts under 5 years (60 months)
        if name in WHOLE_NUT_NAMES and age < 60:
            report.add(SafetyViolation(
                ingredient=raw_name,
                rule=(
                    "Whole nuts are a choking risk for children under 5 years "
                    "(NHS/INSPQ). Use finely ground nut flour or smooth nut butter instead."
                ),
                severity="forbidden",
            ))

        # 3. Choking risk — preparation note (not forbidden, just must be prepared correctly)
        if name in CHOKING_RISK:
            report.add(SafetyViolation(
                ingredient=raw_name,
                rule=CHOKING_RISK[name],
                severity="preparation_note",
                preparation_note=CHOKING_RISK[name],
            ))

        # 4. High sodium — warn
        if name in HIGH_SODIUM:
            report.add(SafetyViolation(
                ingredient=raw_name,
                rule=(
                    f"'{raw_name}' is high in sodium. "
                    "Children 1–3 years should have <1200mg sodium/day (CDC). "
                    "Omit or use a very small amount."
                ),
                severity="warning",
            ))

    return report


def enforce_sodium_rule(recipe_json: str, toddler_age_months: int) -> str:
    """Remove or annotate salt additions in a recipe JSON string for toddlers.

    Operates on the text level since recipe_json structure varies.
    Returns the modified recipe JSON string.
    """
    if toddler_age_months >= 36:  # 3 years+, lighter restrictions
        return recipe_json
    # Replace any "add salt" instructions with a toddler note
    import re
    modified = re.sub(
        r"(?i)\badd\s+(a\s+)?(pinch\s+of\s+)?salt\b",
        "do not add salt (toddler portion)",
        recipe_json,
    )
    return modified
