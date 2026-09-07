"""add_dish_cuisine

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-07
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dish", sa.Column("cuisine", sa.String, nullable=True))
    op.create_index("ix_dish_cuisine", "dish", ["cuisine"])


def downgrade() -> None:
    op.drop_index("ix_dish_cuisine", table_name="dish")
    op.drop_column("dish", "cuisine")
