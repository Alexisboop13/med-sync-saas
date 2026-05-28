"""add performance indexes: clinic_month, patients_clinic_code, audit_payload_gin

Three CONCURRENTLY indexes for query performance:
  ix_appts_clinic_month   – partial index on completed appointments for clinic dashboard queries.
  ix_patients_clinic_code – unique index enforcing one medical_record_code per clinic.
  ix_audit_payload_gin    – GIN index for JSONB containment searches on audit_logs.payload.

Upgrade pre-check: aborts with a RuntimeError if duplicate (clinic_id, medical_record_code)
rows exist, so the unique index build never fails mid-flight.

Revision ID: k1f2g3h4i5j6
Revises: j0e1f2g3h4i5
Create Date: 2026-05-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k1f2g3h4i5j6"
down_revision: Union[str, Sequence[str], None] = "j0e1f2g3h4i5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_IX_CLINIC_MONTH = "ix_appts_clinic_month"
_IX_CLINIC_CODE = "ix_patients_clinic_code"
_IX_AUDIT_GIN = "ix_audit_payload_gin"


def upgrade() -> None:
    # Abort early if the unique index would fail due to pre-existing duplicates.
    conn = op.get_bind()
    dupes = conn.execute(
        sa.text(
            "SELECT clinic_id, medical_record_code, COUNT(*) AS n "
            "FROM patients "
            "WHERE medical_record_code IS NOT NULL "
            "GROUP BY clinic_id, medical_record_code "
            "HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dupes:
        rows = [(str(r[0]), r[1], r[2]) for r in dupes]
        raise RuntimeError(
            "Cannot create ix_patients_clinic_code — duplicate "
            f"(clinic_id, medical_record_code) pairs found: {rows}"
        )

    with op.get_context().autocommit_block():
        op.create_index(
            _IX_CLINIC_MONTH,
            "appointments",
            ["clinic_id", sa.text("starts_at DESC")],
            postgresql_where=sa.text("status = 'completed'"),
            postgresql_concurrently=True,
        )
        op.create_index(
            _IX_CLINIC_CODE,
            "patients",
            ["clinic_id", "medical_record_code"],
            unique=True,
            postgresql_concurrently=True,
        )
        op.create_index(
            _IX_AUDIT_GIN,
            "audit_logs",
            ["payload"],
            postgresql_using="gin",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _IX_AUDIT_GIN,
            table_name="audit_logs",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            _IX_CLINIC_CODE,
            table_name="patients",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            _IX_CLINIC_MONTH,
            table_name="appointments",
            postgresql_concurrently=True,
            if_exists=True,
        )
