"""add location_id to appointments

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column(
            "location_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Branch/location where the appointment takes place.",
        ),
    )

    op.create_foreign_key(
        "fk_appointments_location_id",
        "appointments",
        "locations",
        ["location_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_appointments_location_id", "appointments", ["location_id"])


def downgrade() -> None:
    op.drop_index("ix_appointments_location_id", table_name="appointments")
    op.drop_constraint("fk_appointments_location_id", "appointments", type_="foreignkey")
    op.drop_column("appointments", "location_id")
