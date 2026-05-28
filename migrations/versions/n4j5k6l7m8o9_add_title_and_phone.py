"""Add title to doctors and phone_enc to users

Revision ID: n4j5k6l7m8o9
Revises: m3i4j5k6l7n8
Create Date: 2026-05-17
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n4j5k6l7m8o9"
down_revision: Union[str, Sequence[str], None] = "m3i4j5k6l7n8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column("title", sa.String(10), nullable=False, server_default="Dr."),
    )
    op.add_column(
        "users",
        sa.Column("phone_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("doctors", "title")
    op.drop_column("users", "phone_enc")
