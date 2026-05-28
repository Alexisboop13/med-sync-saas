"""add email_verifications table

Stores 6-digit verification codes for the public booking email-verification flow.

Revision ID: r8n9o0p1q2s3
Revises: q7m8n9o0p1r2
Create Date: 2026-05-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "r8n9o0p1q2s3"
down_revision: Union[str, Sequence[str], None] = "q7m8n9o0p1r2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_verifications",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("code", sa.String(6), nullable=False),
        sa.Column(
            "send_count",
            sa.SmallInteger,
            nullable=False,
            server_default="1",
            comment="Sends within the current 1-hour rate-limit window.",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_token", sa.String(36), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index(
        "ix_email_verifications_email_hash",
        "email_verifications",
        ["email_hash"],
        unique=True,
    )
    op.create_index(
        "ix_email_verifications_verification_token",
        "email_verifications",
        ["verification_token"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_email_verifications_verification_token", table_name="email_verifications")
    op.drop_index("ix_email_verifications_email_hash", table_name="email_verifications")
    op.drop_table("email_verifications")
