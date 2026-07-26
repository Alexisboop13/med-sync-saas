"""widen medical_record_code to support manual codes

Owners can now set a manual medical_record_code (free alphanumeric, up to
20 chars) to match a pre-existing physical medical record, in addition to
the auto-generated 4-char codes. The existing UNIQUE indexes on
(clinic_id, medical_record_code) already enforce per-clinic uniqueness at
the DB level and are untouched by this migration.

Revision ID: y6z7a8b9c0d1
Revises: w3x4y5z6a7b8
Create Date: 2026-07-26 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y6z7a8b9c0d1"
down_revision: Union[str, Sequence[str], None] = "w3x4y5z6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "patients",
        "medical_record_code",
        type_=sa.String(20),
        existing_type=sa.String(4),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "patients",
        "medical_record_code",
        type_=sa.String(4),
        existing_type=sa.String(20),
        existing_nullable=False,
    )
