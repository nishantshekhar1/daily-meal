"""Alembic migration environment."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

# Make the app importable from here
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import all models so SQLModel.metadata is fully populated
from app.models import (  # noqa: F401
    CanonicalIngredient,
    IngredientAlias,
    StockLot,
    Receipt,
    ReceiptLine,
    HouseholdMember,
    Dish,
    MealPlan,
    PlannedMeal,
    CookEvent,
    CookDeduction,
    PlanningSession,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def get_url() -> str:
    """Prefer DB_URL env var; fall back to app settings."""
    if url := os.getenv("DATABASE_URL"):
        return url
    # Lazy import to avoid circular startup at migration time
    from app.core.config import get_settings
    return get_settings().db_url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE support
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
