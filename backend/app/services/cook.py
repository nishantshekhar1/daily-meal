"""Cook-and-deplete service.

When a user marks a meal as cooked:
1. Deduct recipe ingredient quantities from stock lots (FIFO)
2. Compute exhaustion candidates (lots that hit 0 or near-0)
3. Persist a CookEvent and CookDeduction rows
4. Return the candidates list for the user to confirm
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import (
    CanonicalIngredient,
    CookDeduction,
    CookEvent,
    Dish,
    PlannedMeal,
    StockLot,
)
from app.services.unit_utils import convert

logger = logging.getLogger(__name__)


def cook_meal(db: Session, planned_meal_id: int) -> dict:
    """Mark a planned meal as cooked, deduct stock, return exhaustion candidates.

    Returns:
        {
          cook_event_id: int,
          deductions: [...],
          exhaustion_candidates: [{"lot_id", "ingredient_name", "quantity", "unit"}],
        }
    """
    planned = db.get(PlannedMeal, planned_meal_id)
    if planned is None:
        raise ValueError(f"PlannedMeal {planned_meal_id} not found")
    if planned.cooked:
        raise ValueError(f"PlannedMeal {planned_meal_id} already cooked")

    dish = db.get(Dish, planned.dish_id)
    if dish is None or not dish.recipe_json:
        raise ValueError(f"Dish for planned meal {planned_meal_id} has no recipe")

    recipe = json.loads(dish.recipe_json)
    ingredients = recipe.get("ingredients", [])

    cook_event = CookEvent(planned_meal_id=planned_meal_id, cooked_at=datetime.utcnow())
    db.add(cook_event)
    db.flush()

    deductions = []
    exhaustion_candidates = []

    for ing_req in ingredients:
        ing_name = ing_req.get("name", "")
        needed = float(ing_req.get("quantity", 0))
        needed_unit = ing_req.get("unit", "g")
        if needed <= 0:
            continue

        # Find stock lots for this ingredient (FIFO)
        ing_stmt = (
            select(StockLot, CanonicalIngredient)
            .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
            .where(CanonicalIngredient.name == ing_name)
            .where(StockLot.quantity > 0)
            .order_by(StockLot.acquired_at)
        )
        rows = db.exec(ing_stmt).all()

        remaining = needed
        for lot, ing in rows:
            if remaining <= 0:
                break
            density = ing.density_g_per_ml
            lot_unit = lot.unit
            try:
                lot_avail_in_recipe_unit = convert(lot.quantity, lot_unit, needed_unit, density)
            except ValueError:
                continue

            take_recipe = min(remaining, lot_avail_in_recipe_unit)
            try:
                take_lot = convert(take_recipe, needed_unit, lot_unit, density)
            except ValueError:
                continue

            lot.quantity = max(0.0, lot.quantity - take_lot)
            lot_exhausted = lot.quantity == 0.0
            db.add(lot)

            deduction = CookDeduction(
                cook_event_id=cook_event.id,
                stock_lot_id=lot.id,
                quantity_deducted=take_lot,
                unit=lot_unit,
                lot_exhausted=lot_exhausted,
            )
            db.add(deduction)
            deductions.append({
                "lot_id": lot.id,
                "ingredient_name": ing.name,
                "quantity_deducted": take_lot,
                "unit": lot_unit,
                "lot_exhausted": lot_exhausted,
            })
            remaining -= take_recipe

            if lot_exhausted:
                exhaustion_candidates.append({
                    "lot_id": lot.id,
                    "ingredient_name": ing.name,
                    "quantity": 0.0,
                    "unit": lot_unit,
                })

    planned.cooked = True
    planned.cooked_at = datetime.utcnow()
    db.add(planned)
    db.commit()

    return {
        "cook_event_id": cook_event.id,
        "deductions": deductions,
        "exhaustion_candidates": exhaustion_candidates,
    }


def confirm_exhaustion(
    db: Session,
    cook_event_id: int,
    remove_lot_ids: list[int],
) -> None:
    """User confirms which exhausted lots to mark as fully removed from stock.

    Lots NOT in remove_lot_ids stay at 0 quantity (still visible, not hidden).
    """
    cook_event = db.get(CookEvent, cook_event_id)
    if cook_event is None:
        raise ValueError(f"CookEvent {cook_event_id} not found")

    stmt = select(CookDeduction).where(CookDeduction.cook_event_id == cook_event_id)
    deductions = db.exec(stmt).all()

    remove_set = set(remove_lot_ids)
    for ded in deductions:
        if ded.stock_lot_id in remove_set:
            ded.removed_from_stock = True
            # Zero out the lot quantity fully (may already be 0)
            lot = db.get(StockLot, ded.stock_lot_id)
            if lot:
                lot.quantity = 0.0
                db.add(lot)
        db.add(ded)

    cook_event.confirmed = True
    db.add(cook_event)
    db.commit()
