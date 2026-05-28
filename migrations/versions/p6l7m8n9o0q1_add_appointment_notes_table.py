"""add appointment_notes table

Replaces the single notes_enc field on appointments with a proper
per-note history table. Each note is a separate row with author and timestamp.

Revision ID: p6l7m8n9o0q1
Revises: o5k6l7m8n9p0
Create Date: 2026-05-17 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "p6l7m8n9o0q1"
down_revision: Union[str, Sequence[str], None] = "o5k6l7m8n9p0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "appointment_notes"


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
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_by_id",
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

    op.create_index("ix_appointment_notes_clinic_id", _TABLE, ["clinic_id"])
    op.create_index("ix_appointment_notes_appointment_id", _TABLE, ["appointment_id"])
    op.create_index(
        "ix_appointment_notes_clinic_appt",
        _TABLE,
        ["clinic_id", "appointment_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_appointment_notes_clinic_appt", table_name=_TABLE)
    op.drop_index("ix_appointment_notes_appointment_id", table_name=_TABLE)
    op.drop_index("ix_appointment_notes_clinic_id", table_name=_TABLE)
    op.drop_table(_TABLE)
