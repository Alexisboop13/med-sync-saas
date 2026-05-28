"""remove_license_number_from_doctors

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("doctors", "license_number")


def downgrade() -> None:
    op.add_column(
        "doctors",
        sa.Column(
            "license_number",
            sa.String(length=50),
            nullable=True,
            comment="Professional medical license (Cédula Profesional in Mexico).",
        ),
    )
