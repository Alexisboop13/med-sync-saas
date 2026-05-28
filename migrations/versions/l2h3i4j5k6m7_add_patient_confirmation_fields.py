"""add patient_confirmed_at and patient_confirmation_channel to appointments

Tracks when and how a patient confirmed attendance.

patient_confirmed_at       — timestamp the patient confirmed (NULL = not yet confirmed)
patient_confirmation_channel — how they confirmed: magic_link | whatsapp | phone | staff

Revision ID: l2h3i4j5k6m7
Revises: k1f2g3h4i5j6
Create Date: 2026-05-04 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l2h3i4j5k6m7"
down_revision: Union[str, Sequence[str], None] = "k1f2g3h4i5j6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "appointments"
_IDX = "idx_appts_confirmed"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "patient_confirmed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "patient_confirmation_channel",
            sa.String(20),
            nullable=True,
        ),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            _IDX,
            _TABLE,
            ["patient_confirmed_at"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(_IDX, table_name=_TABLE, postgresql_concurrently=True, if_exists=True)
    op.drop_column(_TABLE, "patient_confirmation_channel")
    op.drop_column(_TABLE, "patient_confirmed_at")
