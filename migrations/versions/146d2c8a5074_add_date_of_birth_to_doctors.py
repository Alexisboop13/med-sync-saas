"""add date_of_birth_enc to doctors

Revision ID: 146d2c8a5074
Revises: w3x4y5z6a7b8
Create Date: 2026-07-25 22:12:43.844464

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "146d2c8a5074"
down_revision: Union[str, Sequence[str], None] = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column(
            "date_of_birth_enc",
            sa.Text(),
            nullable=True,
            comment="Encrypted ISO-8601 date string: '1990-04-15'.",
        ),
    )


def downgrade() -> None:
    op.drop_column("doctors", "date_of_birth_enc")
