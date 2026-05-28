"""add reschedule_requests table

Patients request a reschedule via their magic link. Staff reviews and marks
each request as resolved or ignored.

Revision ID: m3i4j5k6l7n8
Revises: l2h3i4j5k6m7
Create Date: 2026-05-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "m3i4j5k6l7n8"
down_revision: Union[str, Sequence[str], None] = "l2h3i4j5k6m7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "reschedule_requests"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "appointment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("appointments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("patient_note", sa.Text, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_index("ix_reschedule_requests_clinic_id", _TABLE, ["clinic_id"])
    op.create_index("ix_reschedule_requests_appointment_id", _TABLE, ["appointment_id"])
    op.create_index(
        "ix_reschedule_requests_clinic_status",
        _TABLE,
        ["clinic_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_reschedule_requests_clinic_status", table_name=_TABLE)
    op.drop_index("ix_reschedule_requests_appointment_id", table_name=_TABLE)
    op.drop_index("ix_reschedule_requests_clinic_id", table_name=_TABLE)
    op.drop_table(_TABLE)
