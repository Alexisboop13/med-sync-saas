"""add notify_email to clinics

Per-clinic notification email for reschedule requests.
When set, overrides the global CLINIC_NOTIFY_EMAIL env var.

Revision ID: s9o0p1q2r3t4
Revises: r8n9o0p1q2s3
Create Date: 2026-05-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s9o0p1q2r3t4"
down_revision: Union[str, Sequence[str], None] = "r8n9o0p1q2s3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clinics",
        sa.Column(
            "notify_email",
            sa.String(254),
            nullable=True,
            comment="Inbox for patient reschedule-request notifications. Falls back to owner email.",
        ),
    )


def downgrade() -> None:
    op.drop_column("clinics", "notify_email")
