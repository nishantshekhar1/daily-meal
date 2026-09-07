"""Meal plan entities: Dish, MealPlan, PlannedMeal."""
import enum
from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class MealSlot(str, enum.Enum):
    breakfast = "breakfast"
    lunch = "lunch"
    dinner = "dinner"
    snack = "snack"


class MealAudience(str, enum.Enum):
    main = "main"       # adults + children
    toddler = "toddler"


class Dish(SQLModel, table=True):
    """A concrete dish the agent has generated at least once.

    Stored so the agent can refer back to past successful dishes and avoid
    repeating the same suggestion within a short window.
    """

    __tablename__ = "dish"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = Field(default=None)
    recipe_json: Optional[str] = Field(
        default=None,
        description="Full recipe as JSON string (ingredients with quantities, steps).",
    )
    suitable_slots: str = Field(
        default="breakfast,lunch,dinner",
        description="Comma-separated MealSlot values this dish works for.",
    )
    audience: MealAudience = Field(default=MealAudience.main)
    cuisine: Optional[str] = Field(
        default=None,
        index=True,
        description="Lowercase cuisine tag (e.g. indian) used for preference ranking.",
    )
    last_suggested_at: Optional[datetime] = Field(default=None)

    planned_meals: list["PlannedMeal"] = Relationship(back_populates="dish")


class MealPlan(SQLModel, table=True):
    """A weekly plan covering Mon–Sun (or any date range).

    Multiple plans can coexist; the latest non-archived one is the active plan.
    """

    __tablename__ = "meal_plan"

    id: Optional[int] = Field(default=None, primary_key=True)
    week_start: date
    created_at: datetime = Field(default_factory=datetime.utcnow)
    session_id: Optional[str] = Field(default=None)   # links to PlanningSession.id
    archived: bool = Field(default=False)

    planned_meals: list["PlannedMeal"] = Relationship(back_populates="plan")


class SuggestionCache(SQLModel, table=True):
    """A pre-generated plan for one meal slot, ready to serve instantly.

    Persisted rather than kept in memory only: a plan costs several minutes of
    local GPU time, so a process restart (including a dev reload) must not
    throw it away.  ``fingerprint`` covers the inputs that would change the
    outcome — pantry stock, active members, cuisine priority, plan size — so a
    stale entry is detected instead of served.
    """

    __tablename__ = "suggestion_cache"

    slot: str = Field(primary_key=True)
    fingerprint: str = Field(index=True)
    result_json: str = Field(description="Serialized SuggestResult payload.")
    plan_id: Optional[int] = Field(default=None, foreign_key="meal_plan.id")
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class PlannedMeal(SQLModel, table=True):
    """One meal slot within a plan."""

    __tablename__ = "planned_meal"

    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="meal_plan.id", index=True)
    dish_id: int = Field(foreign_key="dish.id")

    day: date
    slot: MealSlot
    audience: MealAudience = Field(default=MealAudience.main)
    servings: int = Field(default=4, description="Number of people this serves.")
    cooked: bool = Field(default=False)
    cooked_at: Optional[datetime] = Field(default=None)

    plan: Optional[MealPlan] = Relationship(back_populates="planned_meals")
    dish: Optional[Dish] = Relationship(back_populates="planned_meals")
