"""add past_due_since to clinics

Tracks when a subscription entered the past_due state so the 15-day grace
period can be calculated without calling Stripe on every request.

Revision ID: t0u1v2w3x4y5
Revises: s9o0p1q2r3t4
Create Date: 2026-05-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t0u1v2w3x4y5"
down_revision: Union[str, Sequence[str], None] = "s9o0p1q2r3t4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clinics",
        sa.Column(
            "past_due_since",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the subscription first entered past_due. Cleared on payment recovery.",
        ),
    )


def downgrade() -> None:
    op.drop_column("clinics", "past_due_since")
