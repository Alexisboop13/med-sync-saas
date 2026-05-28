"""add locations table

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-05-02 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "locations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(100),
            nullable=False,
            comment="Display name, e.g. 'Jardines de Morelos'.",
        ),
        sa.Column(
            "address",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "google_maps_url",
            sa.String(500),
            nullable=True,
            comment="Google Maps share link for patient-facing booking pages.",
        ),
        sa.Column(
            "rooms",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
            comment=(
                "Consultory/room structure. Schema: "
                "{consultorioN: {units: [...], colors: {unitName: '#hex'}}}."
            ),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_locations_clinic_id", "locations", ["clinic_id"])


def downgrade() -> None:
    op.drop_index("ix_locations_clinic_id", table_name="locations")
    op.drop_table("locations")
