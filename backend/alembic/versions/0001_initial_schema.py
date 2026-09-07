"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-09-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canonical_ingredient",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("category", sa.String, nullable=False, server_default="other"),
        sa.Column("default_unit", sa.String, nullable=False, server_default="g"),
        sa.Column("density_g_per_ml", sa.Float, nullable=True),
        sa.Column("toddler_high_sodium", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("toddler_choking_risk", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("toddler_forbidden_under_months", sa.Integer, nullable=True),
    )
    op.create_index("ix_canonical_ingredient_name", "canonical_ingredient", ["name"])

    op.create_table(
        "ingredient_alias",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("alias", sa.String, nullable=False),
        sa.Column("ingredient_id", sa.Integer, sa.ForeignKey("canonical_ingredient.id"), nullable=False),
        sa.Column("confidence", sa.Float, nullable=False, server_default="1.0"),
        sa.Column("source", sa.String, nullable=False, server_default="seed"),
    )
    op.create_index("ix_ingredient_alias_alias", "ingredient_alias", ["alias"])
    op.create_index("ix_ingredient_alias_ingredient_id", "ingredient_alias", ["ingredient_id"])

    op.create_table(
        "household_member",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("age_months", sa.Integer, nullable=True),
        sa.Column("dietary_notes", sa.String, nullable=True),
        sa.Column("active", sa.Boolean, nullable=False, server_default="1"),
    )

    op.create_table(
        "receipt",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("image_path", sa.String, nullable=False),
        sa.Column("raw_ocr_text", sa.Text, nullable=True),
        sa.Column("store_name", sa.String, nullable=True),
        sa.Column("purchase_date", sa.DateTime, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("uploaded_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "receipt_line",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("receipt_id", sa.Integer, sa.ForeignKey("receipt.id"), nullable=False),
        sa.Column("raw_text", sa.String, nullable=False),
        sa.Column("is_grocery", sa.Boolean, nullable=True),
        sa.Column("quantity", sa.Float, nullable=True),
        sa.Column("unit", sa.String, nullable=True),
        sa.Column("ingredient_id", sa.Integer, sa.ForeignKey("canonical_ingredient.id"), nullable=True),
        sa.Column("match_confidence", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default="0"),
    )
    op.create_index("ix_receipt_line_receipt_id", "receipt_line", ["receipt_id"])

    op.create_table(
        "stock_lot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ingredient_id", sa.Integer, sa.ForeignKey("canonical_ingredient.id"), nullable=False),
        sa.Column("quantity", sa.Float, nullable=False),
        sa.Column("unit", sa.String, nullable=False),
        sa.Column("original_quantity", sa.Float, nullable=False),
        sa.Column("source", sa.String, nullable=False, server_default="manual"),
        sa.Column("receipt_line_id", sa.Integer, sa.ForeignKey("receipt_line.id"), nullable=True),
        sa.Column("acquired_at", sa.DateTime, nullable=False),
        sa.Column("notes", sa.String, nullable=True),
        sa.Column("image_path", sa.String, nullable=True),
    )
    op.create_index("ix_stock_lot_ingredient_id", "stock_lot", ["ingredient_id"])

    op.create_table(
        "meal_plan",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("week_start", sa.Date, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("session_id", sa.String, nullable=True),
        sa.Column("archived", sa.Boolean, nullable=False, server_default="0"),
    )

    op.create_table(
        "planning_session",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_activity_at", sa.DateTime, nullable=False),
        sa.Column("slot", sa.String, nullable=False, server_default="dinner"),
        sa.Column("questions_asked", sa.Integer, nullable=False, server_default="0"),
        sa.Column("questions_max", sa.Integer, nullable=False, server_default="3"),
        sa.Column("questions_json", sa.Text, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="active"),
        sa.Column("result_plan_id", sa.Integer, sa.ForeignKey("meal_plan.id"), nullable=True),
    )

    op.create_table(
        "dish",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("recipe_json", sa.Text, nullable=True),
        sa.Column("suitable_slots", sa.String, nullable=False, server_default="breakfast,lunch,dinner"),
        sa.Column("audience", sa.String, nullable=False, server_default="main"),
        sa.Column("last_suggested_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_dish_name", "dish", ["name"])

    op.create_table(
        "planned_meal",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("meal_plan.id"), nullable=False),
        sa.Column("dish_id", sa.Integer, sa.ForeignKey("dish.id"), nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("slot", sa.String, nullable=False),
        sa.Column("audience", sa.String, nullable=False, server_default="main"),
        sa.Column("servings", sa.Integer, nullable=False, server_default="4"),
        sa.Column("cooked", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("cooked_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_planned_meal_plan_id", "planned_meal", ["plan_id"])

    op.create_table(
        "cook_event",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("planned_meal_id", sa.Integer, sa.ForeignKey("planned_meal.id"), nullable=False),
        sa.Column("cooked_at", sa.DateTime, nullable=False),
        sa.Column("confirmed", sa.Boolean, nullable=False, server_default="0"),
    )
    op.create_index("ix_cook_event_planned_meal_id", "cook_event", ["planned_meal_id"])

    op.create_table(
        "cook_deduction",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("cook_event_id", sa.Integer, sa.ForeignKey("cook_event.id"), nullable=False),
        sa.Column("stock_lot_id", sa.Integer, sa.ForeignKey("stock_lot.id"), nullable=False),
        sa.Column("quantity_deducted", sa.Float, nullable=False),
        sa.Column("unit", sa.String, nullable=False),
        sa.Column("lot_exhausted", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("removed_from_stock", sa.Boolean, nullable=False, server_default="0"),
    )
    op.create_index("ix_cook_deduction_cook_event_id", "cook_deduction", ["cook_event_id"])
    op.create_index("ix_cook_deduction_stock_lot_id", "cook_deduction", ["stock_lot_id"])


def downgrade() -> None:
    op.drop_table("cook_deduction")
    op.drop_table("cook_event")
    op.drop_table("planned_meal")
    op.drop_table("dish")
    op.drop_table("planning_session")
    op.drop_table("meal_plan")
    op.drop_table("stock_lot")
    op.drop_table("receipt_line")
    op.drop_table("receipt")
    op.drop_table("household_member")
    op.drop_table("ingredient_alias")
    op.drop_table("canonical_ingredient")
