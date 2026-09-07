"""Seed the canonical ingredient table with common ingredients and aliases.

Run with:  python -m app.db.seed
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from sqlmodel import Session, select

from app.db.session import create_db_and_tables, engine
from app.models import CanonicalIngredient, IngredientAlias, IngredientCategory

# ──────────────────────────────────────────────────────────────────────────────
# Seed data:  (name, category, default_unit, density_g_per_ml, toddler_flags, [aliases])
# density_g_per_ml: None = solid / count, float = liquid or variable
# toddler_forbidden_under_months: None = safe from weaning (6m+)
# ──────────────────────────────────────────────────────────────────────────────
SEED: list[dict] = [
    # Dairy
    {
        "name": "whole milk",
        "category": IngredientCategory.dairy,
        "default_unit": "mL",
        "density_g_per_ml": 1.03,
        "aliases": ["milk", "full fat milk", "whl mlk", "gv whl mlk gal", "whole milk gallon"],
    },
    {
        "name": "skim milk",
        "category": IngredientCategory.dairy,
        "default_unit": "mL",
        "density_g_per_ml": 1.033,
        "aliases": ["skimmed milk", "fat free milk", "skim"],
    },
    {
        "name": "butter",
        "category": IngredientCategory.dairy,
        "default_unit": "g",
        "density_g_per_ml": 0.91,
        "aliases": ["unsalted butter", "salted butter", "bttr"],
    },
    {
        "name": "cheddar cheese",
        "category": IngredientCategory.dairy,
        "default_unit": "g",
        "aliases": ["cheddar", "shredded cheddar", "sliced cheddar"],
    },
    {
        "name": "eggs",
        "category": IngredientCategory.egg,
        "default_unit": "count",
        "aliases": ["egg", "large eggs", "medium eggs", "eggs dozen", "dz eggs"],
    },
    # Produce
    {
        "name": "onion",
        "category": IngredientCategory.produce,
        "default_unit": "g",
        "aliases": ["yellow onion", "brown onion", "white onion", "onions"],
    },
    {
        "name": "garlic",
        "category": IngredientCategory.produce,
        "default_unit": "count",
        "aliases": ["garlic cloves", "garlic bulb", "garlc"],
    },
    {
        "name": "tomatoes",
        "category": IngredientCategory.produce,
        "default_unit": "g",
        "aliases": ["tomato", "roma tomatoes", "cherry tomatoes", "vine tomatoes"],
        "toddler_choking_risk": True,   # cherry tomatoes – must be quartered
    },
    {
        "name": "carrots",
        "category": IngredientCategory.produce,
        "default_unit": "g",
        "aliases": ["carrot", "baby carrots", "shredded carrot"],
        "toddler_choking_risk": True,   # raw – must be grated for toddlers
    },
    {
        "name": "spinach",
        "category": IngredientCategory.produce,
        "default_unit": "g",
        "aliases": ["baby spinach", "fresh spinach"],
    },
    {
        "name": "potatoes",
        "category": IngredientCategory.produce,
        "default_unit": "g",
        "aliases": ["potato", "russet potatoes", "white potatoes", "red potatoes"],
    },
    {
        "name": "grapes",
        "category": IngredientCategory.produce,
        "default_unit": "g",
        "aliases": ["red grapes", "green grapes", "seedless grapes"],
        "toddler_choking_risk": True,   # must be quartered for toddlers
    },
    # Meat
    {
        "name": "chicken breast",
        "category": IngredientCategory.meat,
        "default_unit": "g",
        "aliases": ["chicken breasts", "boneless chicken breast", "skinless chicken breast"],
    },
    {
        "name": "ground beef",
        "category": IngredientCategory.meat,
        "default_unit": "g",
        "aliases": ["minced beef", "beef mince", "hamburger meat", "lean ground beef"],
    },
    {
        "name": "salmon fillet",
        "category": IngredientCategory.seafood,
        "default_unit": "g",
        "aliases": ["salmon", "atlantic salmon", "salmon fillets"],
    },
    # Grains
    {
        "name": "all-purpose flour",
        "category": IngredientCategory.grain,
        "default_unit": "g",
        "density_g_per_ml": 0.53,
        "aliases": ["flour", "plain flour", "ap flour", "all purpose flour"],
    },
    {
        "name": "rice",
        "category": IngredientCategory.grain,
        "default_unit": "g",
        "aliases": ["white rice", "long grain rice", "basmati rice", "jasmine rice"],
    },
    {
        "name": "pasta",
        "category": IngredientCategory.grain,
        "default_unit": "g",
        "aliases": ["spaghetti", "penne", "fettuccine", "linguine", "rigatoni", "macaroni"],
    },
    {
        "name": "rolled oats",
        "category": IngredientCategory.grain,
        "default_unit": "g",
        "density_g_per_ml": 0.36,
        "aliases": ["oats", "oatmeal", "old fashioned oats", "quick oats"],
    },
    # Oils & condiments
    {
        "name": "olive oil",
        "category": IngredientCategory.oil,
        "default_unit": "mL",
        "density_g_per_ml": 0.92,
        "aliases": ["extra virgin olive oil", "evoo", "olive oil bottle"],
    },
    {
        "name": "vegetable oil",
        "category": IngredientCategory.oil,
        "default_unit": "mL",
        "density_g_per_ml": 0.92,
        "aliases": ["canola oil", "sunflower oil", "cooking oil"],
    },
    {
        "name": "soy sauce",
        "category": IngredientCategory.condiment,
        "default_unit": "mL",
        "density_g_per_ml": 1.19,
        "toddler_high_sodium": True,
        "aliases": ["light soy sauce", "dark soy sauce", "tamari"],
    },
    {
        "name": "salt",
        "category": IngredientCategory.spice,
        "default_unit": "g",
        "toddler_high_sodium": True,
        "aliases": ["table salt", "sea salt", "kosher salt", "fine salt"],
    },
    # Sweeteners
    {
        "name": "honey",
        "category": IngredientCategory.sweetener,
        "default_unit": "g",
        "density_g_per_ml": 1.42,
        "toddler_forbidden_under_months": 12,
        "aliases": ["raw honey", "clover honey", "manuka honey"],
    },
    {
        "name": "sugar",
        "category": IngredientCategory.sweetener,
        "default_unit": "g",
        "aliases": ["white sugar", "granulated sugar", "cane sugar"],
    },
    # Legumes
    {
        "name": "canned chickpeas",
        "category": IngredientCategory.legume,
        "default_unit": "g",
        "aliases": ["chickpeas", "garbanzo beans", "tinned chickpeas"],
    },
    {
        "name": "red lentils",
        "category": IngredientCategory.legume,
        "default_unit": "g",
        "aliases": ["lentils", "split red lentils"],
    },
    # Nuts / seeds
    {
        "name": "almonds",
        "category": IngredientCategory.nut_seed,
        "default_unit": "g",
        "toddler_choking_risk": True,
        "toddler_forbidden_under_months": 60,  # whole nuts – under 5 years
        "aliases": ["whole almonds", "raw almonds", "sliced almonds"],
    },
    {
        "name": "peanut butter",
        "category": IngredientCategory.nut_seed,
        "default_unit": "g",
        "aliases": ["smooth peanut butter", "crunchy peanut butter", "pb"],
    },
    # Canned / frozen
    {
        "name": "canned diced tomatoes",
        "category": IngredientCategory.canned,
        "default_unit": "g",
        "aliases": ["diced tomatoes", "tinned tomatoes", "crushed tomatoes", "chopped tomatoes"],
    },
    {
        "name": "frozen peas",
        "category": IngredientCategory.frozen,
        "default_unit": "g",
        "aliases": ["peas", "garden peas", "petit pois"],
    },
    # Beverage
    {
        "name": "orange juice",
        "category": IngredientCategory.beverage,
        "default_unit": "mL",
        "density_g_per_ml": 1.04,
        "aliases": ["oj", "fresh orange juice", "orange juice carton"],
    },
]


def _upsert_ingredient(session: Session, row: dict) -> CanonicalIngredient:
    name = row["name"]
    stmt = select(CanonicalIngredient).where(CanonicalIngredient.name == name)
    ing = session.exec(stmt).first()
    if ing is None:
        ing = CanonicalIngredient(
            name=name,
            category=row.get("category", IngredientCategory.other),
            default_unit=row.get("default_unit", "g"),
            density_g_per_ml=row.get("density_g_per_ml"),
            toddler_high_sodium=row.get("toddler_high_sodium", False),
            toddler_choking_risk=row.get("toddler_choking_risk", False),
            toddler_forbidden_under_months=row.get("toddler_forbidden_under_months"),
        )
        session.add(ing)
        session.flush()
    return ing


def _upsert_alias(session: Session, ingredient_id: int, alias: str) -> None:
    stmt = select(IngredientAlias).where(IngredientAlias.alias == alias.lower())
    existing = session.exec(stmt).first()
    if existing is None:
        session.add(
            IngredientAlias(
                alias=alias.lower(),
                ingredient_id=ingredient_id,
                confidence=1.0,
                source="seed",
            )
        )


def seed_into(session: Session) -> int:
    """Seed canonical ingredients into an arbitrary session.

    Split out from :func:`seed` so evaluation fixtures can build a throwaway
    database with the same ingredient vocabulary as production — resolution and
    unit conversion are part of what is being evaluated, so they must not be
    stubbed.
    """
    for row in SEED:
        ing = _upsert_ingredient(session, row)
        # Also add the canonical name itself as an alias
        _upsert_alias(session, ing.id, ing.name)
        for alias in row.get("aliases", []):
            _upsert_alias(session, ing.id, alias)
    session.commit()
    return len(SEED)


def seed() -> None:
    create_db_and_tables()
    with Session(engine) as session:
        count = seed_into(session)
    print(f"Seeded {count} ingredients.")


if __name__ == "__main__":
    seed()
