"""add_suggestion_cache

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "suggestion_cache",
        sa.Column("slot", sa.String, primary_key=True),
        sa.Column("fingerprint", sa.String, nullable=False),
        sa.Column("result_json", sa.Text, nullable=False),
        sa.Column("plan_id", sa.Integer, sa.ForeignKey("meal_plan.id"), nullable=True),
        sa.Column("generated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_suggestion_cache_fingerprint", "suggestion_cache", ["fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_suggestion_cache_fingerprint", table_name="suggestion_cache")
    op.drop_table("suggestion_cache")
