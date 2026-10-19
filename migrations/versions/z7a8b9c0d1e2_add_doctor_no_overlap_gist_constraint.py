"""add uq_doctor_no_overlap GIST exclusion constraint on appointments

The service layer (_assert_no_overlap in app/api/routes/appointments.py)
checks for overlapping appointments before every create/move and returns a
friendly 409 — but without a DB-level guard, two concurrent requests that
both pass that check at the same time can still double-book a doctor.

This migration closes that gap with a PostgreSQL exclusion constraint:
  - doctor_id WITH =            → same doctor
  - tstzrange(starts_at, ends_at, '[)') WITH &&  → overlapping time range
  - WHERE status NOT IN (...)   → only active/completed appointments occupy
    the slot; canceled/no-show rows never conflict.

EXCLUDE constraints with a plain equality operator (doctor_id WITH =) need
the btree_gist extension to provide a GiST operator class for equality —
without it, Postgres only knows how to use GiST operator classes for range
types. Render (and every mainstream managed Postgres) ships btree_gist as a
trusted, pre-bundled extension: CREATE EXTENSION does not require superuser.

SQLAlchemy's Mapped API has no ExcludeConstraint support, so this lives
entirely in raw SQL here (see CLAUDE.md).

Revision ID: z7a8b9c0d1e2
Revises: y6z7a8b9c0d1
Create Date: 2026-10-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "z7a8b9c0d1e2"
down_revision: Union[str, Sequence[str], None] = "y6z7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        ALTER TABLE appointments
        ADD CONSTRAINT uq_doctor_no_overlap
        EXCLUDE USING GIST (
          doctor_id WITH =,
          tstzrange(starts_at, ends_at, '[)') WITH &&
        )
        WHERE (status NOT IN ('canceled', 'canceled_by_patient', 'no_show'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP CONSTRAINT uq_doctor_no_overlap")
