"""update doctor specialty default value to spanish

The ORM-level default for Doctor.specialty changed from "General Practice"
to "Medicina general" (app/models/doctor.py). That only affects new rows —
this migration updates existing doctors that still carry the old English
default so the whole fleet reads consistently in Spanish.

Revision ID: a8b9c0d1e2f3
Revises: z7a8b9c0d1e2
Create Date: 2026-10-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "z7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE doctors SET specialty = 'Medicina general' WHERE specialty = 'General Practice'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE doctors SET specialty = 'General Practice' WHERE specialty = 'Medicina general'"
    )
