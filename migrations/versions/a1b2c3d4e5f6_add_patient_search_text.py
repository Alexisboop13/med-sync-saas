"""add patient search_text for trigram search

Revision ID: a1b2c3d4e5f6
Revises: 83194d6f64c9
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "83194d6f64c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.add_column(
        "patients",
        sa.Column(
            "search_text",
            sa.String(500),
            nullable=False,
            server_default="",
            comment=(
                "Lowercase concat of full_name, email, phone for pg_trgm ILIKE search. "
                "Kept in sync with encrypted fields on every write."
            ),
        ),
    )

    op.create_index(
        "ix_patients_search_text_gin",
        "patients",
        ["search_text"],
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_patients_search_text_gin", table_name="patients")
    op.drop_column("patients", "search_text")
