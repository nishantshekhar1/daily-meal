"""Deterministic inventory allocator.

The LLM proposes dishes with ingredient quantities. This module checks whether
the pantry can actually cover them and reserves stock across the week.

Design principle: the model never does inventory arithmetic. All bookkeeping is
here in Python so errors are deterministic and testable.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from app.models import CanonicalIngredient, StockLot
from app.services.unit_utils import convert

logger = logging.getLogger(__name__)


@dataclass
class DishRequirement:
    dish_name: str
    ingredients: list[dict]  # [{"name": str, "quantity": float, "unit": str}, ...]


@dataclass
class AllocationResult:
    feasible: bool
    infeasible_ingredients: list[str] = field(default_factory=list)
    # lot_id → amount reserved (for committing after cook confirmation)
    reservations: dict[int, float] = field(default_factory=dict)


class InventoryAllocator:
    """Allocates pantry stock to a list of dishes.

    Usage:
        allocator = InventoryAllocator(session)
        result = allocator.allocate([dish1, dish2, ...])
        if result.feasible:
            # proceed; reservations stored in result.reservations
    """

    def __init__(self, session: Session) -> None:
        self._db = session
        # Working copy of available quantities: lot_id → remaining quantity (in lot's unit)
        self._available: dict[int, float] = {}
        self._lot_units: dict[int, str] = {}
        self._lot_ingredient: dict[int, int] = {}  # lot_id → ingredient_id
        self._ingredient_lots: dict[int, list[int]] = defaultdict(list)  # ingredient_id → [lot_ids]
        self._densities: dict[int, Optional[float]] = {}
        self._ing_names: dict[int, str] = {}
        self._load_stock()

    def _load_stock(self) -> None:
        stmt = (
            select(StockLot, CanonicalIngredient)
            .join(CanonicalIngredient, StockLot.ingredient_id == CanonicalIngredient.id)
            .where(StockLot.quantity > 0)
            .order_by(StockLot.acquired_at)  # FIFO: oldest first
        )
        for lot, ing in self._db.exec(stmt).all():
            self._available[lot.id] = lot.quantity
            self._lot_units[lot.id] = lot.unit
            self._lot_ingredient[lot.id] = ing.id
            self._ingredient_lots[ing.id].append(lot.id)
            self._densities[ing.id] = ing.density_g_per_ml
            self._ing_names[ing.id] = ing.name

    def _find_ingredient_id(self, name: str) -> Optional[int]:
        from sqlmodel import select
        from app.models import IngredientAlias
        stmt = select(IngredientAlias).where(IngredientAlias.alias == name.lower())
        alias = self._db.exec(stmt).first()
        if alias:
            return alias.ingredient_id
        stmt2 = select(CanonicalIngredient).where(CanonicalIngredient.name == name)
        ing = self._db.exec(stmt2).first()
        return ing.id if ing else None

    def allocate(self, dishes: list[DishRequirement]) -> AllocationResult:
        """Allocate stock for all dishes; returns feasibility and reservations."""
        reservations: dict[int, float] = defaultdict(float)
        infeasible: list[str] = []

        for dish in dishes:
            for req in dish.ingredients:
                ing_id = self._find_ingredient_id(req["name"])
                if ing_id is None:
                    infeasible.append(req["name"])
                    continue

                needed = req["quantity"]
                needed_unit = req["unit"]
                lot_ids = self._ingredient_lots.get(ing_id, [])
                density = self._densities.get(ing_id)

                remaining_needed = needed
                for lot_id in lot_ids:
                    if remaining_needed <= 0:
                        break
                    available_in_lot = self._available[lot_id] - reservations.get(lot_id, 0)
                    if available_in_lot <= 0:
                        continue
                    lot_unit = self._lot_units[lot_id]
                    # Convert lot quantity to recipe's unit for comparison
                    try:
                        lot_in_recipe_unit = convert(
                            available_in_lot, lot_unit, needed_unit, density
                        )
                    except ValueError:
                        # Units incompatible — skip this lot
                        continue

                    take_recipe_unit = min(remaining_needed, lot_in_recipe_unit)
                    # Convert back to lot's unit to track reservation
                    try:
                        take_lot_unit = convert(take_recipe_unit, needed_unit, lot_unit, density)
                    except ValueError:
                        continue

                    reservations[lot_id] = reservations.get(lot_id, 0) + take_lot_unit
                    remaining_needed -= take_recipe_unit

                if remaining_needed > 1e-6:  # floating-point slop
                    infeasible.append(req["name"])

        return AllocationResult(
            feasible=len(infeasible) == 0,
            infeasible_ingredients=list(set(infeasible)),
            reservations=dict(reservations),
        )

    def commit_reservations(self, reservations: dict[int, float]) -> None:
        """Deduct reserved quantities from the actual stock lots.

        Call this after the user marks a meal as cooked and confirms deductions.
        """
        for lot_id, amount in reservations.items():
            lot = self._db.get(StockLot, lot_id)
            if lot is None:
                continue
            lot.quantity = max(0.0, lot.quantity - amount)
            self._db.add(lot)
        self._db.commit()
