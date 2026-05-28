"""add reschedule fields to appointments

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "g7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column(
            "proposed_starts_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Staff-proposed new start time; NULL when no reschedule is pending.",
        ),
    )
    op.add_column(
        "appointments",
        sa.Column(
            "proposed_ends_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Staff-proposed new end time; NULL when no reschedule is pending.",
        ),
    )
    op.add_column(
        "appointments",
        sa.Column(
            "reschedule_token",
            sa.String(36),
            nullable=True,
            comment="UUID v4 token for patient confirm/reject reschedule link. Cleared after use.",
        ),
    )
    op.add_column(
        "appointments",
        sa.Column(
            "reschedule_token_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Reschedule token expiry. Typically now + 48 h.",
        ),
    )

    op.create_unique_constraint(
        "uq_appointments_reschedule_token",
        "appointments",
        ["reschedule_token"],
    )
    op.create_index(
        "ix_appointments_reschedule_token",
        "appointments",
        ["reschedule_token"],
    )


def downgrade() -> None:
    op.drop_index("ix_appointments_reschedule_token", table_name="appointments")
    op.drop_constraint("uq_appointments_reschedule_token", "appointments", type_="unique")
    op.drop_column("appointments", "reschedule_token_expires_at")
    op.drop_column("appointments", "reschedule_token")
    op.drop_column("appointments", "proposed_ends_at")
    op.drop_column("appointments", "proposed_starts_at")
