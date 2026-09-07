"""Shared fixtures: an in-memory database and small factories for meal data."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401  (populates SQLModel.metadata)
from app.models import Dish, MealAudience, MealPlan, MealSlot, PlannedMeal


@pytest.fixture
def db():
    """A fresh in-memory database per test.

    StaticPool keeps every connection pointed at the same in-memory database,
    which SQLite otherwise scopes per-connection.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture
def make_meal(db):
    """Factory returning a persisted (plan, dish, planned_meal) triple.

    Recipes are stored the way the planner stores them so that ingredient-scope
    aggregation exercises the real parsing path.
    """

    def _make(
        name: str = "Chana Masala",
        cuisine: str = "indian",
        ingredients: Optional[list[str]] = None,
        audience: MealAudience = MealAudience.main,
        slot: MealSlot = MealSlot.dinner,
        plan: Optional[MealPlan] = None,
        created_at: Optional[datetime] = None,
        cooked: bool = False,
    ):
        if plan is None:
            plan = MealPlan(
                week_start=date.today(),
                created_at=created_at or datetime.utcnow(),
            )
            db.add(plan)
            db.commit()
            db.refresh(plan)

        recipe = {
            "recipe": {
                "servings": 4,
                "prep_minutes": 10,
                "cook_minutes": 20,
                "ingredients": [
                    {"name": n, "quantity": 1, "unit": "cup", "preparation": "diced"}
                    for n in (ingredients or ["chickpeas", "onion", "tomato"])
                ],
                "steps": ["Cook it."],
            }
        }
        import json

        dish = Dish(
            name=name,
            description=f"{name} description",
            cuisine=cuisine,
            audience=audience,
            recipe_json=json.dumps(recipe),
            last_suggested_at=datetime.utcnow(),
        )
        db.add(dish)
        db.commit()
        db.refresh(dish)

        planned = PlannedMeal(
            plan_id=plan.id,
            dish_id=dish.id,
            day=date.today(),
            slot=slot,
            audience=audience,
            servings=4,
            cooked=cooked,
        )
        db.add(planned)
        db.commit()
        db.refresh(planned)
        return plan, dish, planned

    return _make


@pytest.fixture
def aged_plan(db):
    """A plan created far enough in the past to be swept as skipped."""

    def _make(hours_ago: int = 48) -> MealPlan:
        plan = MealPlan(
            week_start=date.today(),
            created_at=datetime.utcnow() - timedelta(hours=hours_ago),
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)
        return plan

    return _make
