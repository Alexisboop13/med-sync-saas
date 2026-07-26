"""add first_name_enc and last_name_enc to patients

Revision ID: w3x4y5z6a7b8
Revises: 146d2c8a5074
Create Date: 2026-07-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = "w3x4y5z6a7b8"
down_revision: Union[str, Sequence[str], None] = "146d2c8a5074"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ADD COLUMN IF NOT EXISTS: Neon ya tiene estas columnas aplicadas de
    # forma manual antes de que esta migración se subiera al repo — sin el
    # guard, esta migración fallaría con DuplicateColumn en ese entorno,
    # pero sigue creando las columnas normalmente en cualquier otro donde
    # de verdad no existan.
    op.execute(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS first_name_enc TEXT"
    )
    op.execute(
        "COMMENT ON COLUMN patients.first_name_enc IS "
        "'Encrypted. Derived from full_name: everything before the last word.'"
    )
    op.execute(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS last_name_enc TEXT"
    )
    op.execute(
        "COMMENT ON COLUMN patients.last_name_enc IS "
        "'Encrypted. Derived from full_name: the last word.'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS last_name_enc")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS first_name_enc")
