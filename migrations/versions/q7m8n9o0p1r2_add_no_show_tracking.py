"""add no-show tracking to patients and appointments

Adds:
  patients.no_show_count    INTEGER NOT NULL DEFAULT 0
  patients.last_no_show_at  TIMESTAMPTZ NULL
  appointments.was_no_show  BOOLEAN NOT NULL DEFAULT FALSE

Revision ID: q7m8n9o0p1r2
Revises: p6l7m8n9o0q1
Create Date: 2026-05-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q7m8n9o0p1r2"
down_revision: Union[str, Sequence[str], None] = "p6l7m8n9o0q1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── patients ──────────────────────────────────────────────────────────────
    op.add_column(
        "patients",
        sa.Column(
            "no_show_count",
            sa.Integer,
            nullable=False,
            server_default="0",
            comment="Cumulative count of no-show appointments for this patient.",
        ),
    )
    op.add_column(
        "patients",
        sa.Column(
            "last_no_show_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp of the most recent no-show event.",
        ),
    )

    # Index to quickly find patients with repeated no-shows (e.g. >= 2)
    op.create_index(
        "ix_patients_clinic_no_show",
        "patients",
        ["clinic_id", "no_show_count"],
    )

    # ── appointments ──────────────────────────────────────────────────────────
    op.add_column(
        "appointments",
        sa.Column(
            "was_no_show",
            sa.Boolean,
            nullable=False,
            server_default="false",
            comment=(
                "True when this appointment was explicitly marked as no-show. "
                "Preserved even if status is later changed, enabling reporting."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("appointments", "was_no_show")
    op.drop_index("ix_patients_clinic_no_show", table_name="patients")
    op.drop_column("patients", "last_no_show_at")
    op.drop_column("patients", "no_show_count")
