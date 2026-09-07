"""add_feedback_tables

Adds the suggestion feedback loop: raw signal log, learned preference
aggregate, and periodic quality metrics.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dish_feedback",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "planned_meal_id", sa.Integer, sa.ForeignKey("planned_meal.id"), nullable=True
        ),
        sa.Column("dish_id", sa.Integer, sa.ForeignKey("dish.id"), nullable=False),
        sa.Column("signal", sa.String, nullable=False),
        sa.Column("reason", sa.String, nullable=True),
        sa.Column("audience", sa.String, nullable=False, server_default="main"),
        sa.Column("cuisine", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_dish_feedback_planned_meal_id", "dish_feedback", ["planned_meal_id"])
    op.create_index("ix_dish_feedback_dish_id", "dish_feedback", ["dish_id"])
    op.create_index("ix_dish_feedback_signal", "dish_feedback", ["signal"])
    op.create_index("ix_dish_feedback_audience", "dish_feedback", ["audience"])
    op.create_index("ix_dish_feedback_cuisine", "dish_feedback", ["cuisine"])
    op.create_index("ix_dish_feedback_created_at", "dish_feedback", ["created_at"])

    op.create_table(
        "preference_profile",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("scope", sa.String, nullable=False),
        sa.Column("scope_key", sa.String, nullable=False),
        sa.Column("audience", sa.String, nullable=False, server_default="main"),
        sa.Column("positive", sa.Float, nullable=False, server_default="0"),
        sa.Column("negative", sa.Float, nullable=False, server_default="0"),
        sa.Column("observations", sa.Integer, nullable=False, server_default="0"),
        sa.Column("score", sa.Float, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("scope", "scope_key", "audience", name="uq_preference_scope"),
    )
    op.create_index("ix_preference_profile_scope", "preference_profile", ["scope"])
    op.create_index("ix_preference_profile_scope_key", "preference_profile", ["scope_key"])
    op.create_index("ix_preference_profile_audience", "preference_profile", ["audience"])
    op.create_index("ix_preference_profile_updated_at", "preference_profile", ["updated_at"])

    op.create_table(
        "metric_snapshot",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("captured_at", sa.DateTime, nullable=False),
        sa.Column("window_days", sa.Integer, nullable=False, server_default="30"),
        sa.Column("suggestions_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cooked_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("distinct_dishes", sa.Integer, nullable=False, server_default="0"),
        sa.Column("cook_through_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("thumbs_up_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("reroll_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("repetition_rate", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_metric_snapshot_captured_at", "metric_snapshot", ["captured_at"])


def downgrade() -> None:
    op.drop_index("ix_metric_snapshot_captured_at", table_name="metric_snapshot")
    op.drop_table("metric_snapshot")

    for ix in (
        "ix_preference_profile_updated_at",
        "ix_preference_profile_audience",
        "ix_preference_profile_scope_key",
        "ix_preference_profile_scope",
    ):
        op.drop_index(ix, table_name="preference_profile")
    op.drop_table("preference_profile")

    for ix in (
        "ix_dish_feedback_created_at",
        "ix_dish_feedback_cuisine",
        "ix_dish_feedback_audience",
        "ix_dish_feedback_signal",
        "ix_dish_feedback_dish_id",
        "ix_dish_feedback_planned_meal_id",
    ):
        op.drop_index(ix, table_name="dish_feedback")
    op.drop_table("dish_feedback")
