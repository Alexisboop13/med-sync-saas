"""add overlap check index on appointments

Partial index on (doctor_id, starts_at, ends_at) excluding terminal statuses.
Used by the overlap-check query that runs before every appointment INSERT/UPDATE.

Why CONCURRENTLY:
  Regular CREATE INDEX takes an AccessShareLock that blocks concurrent writes
  for the duration of the build. On a live table with ongoing bookings this
  would cause write timeouts. CONCURRENTLY builds the index in the background,
  only acquiring brief share-update-exclusive locks, so inserts keep working.

Why partial (WHERE status NOT IN (...)):
  Canceled/no-show rows will never be returned by an overlap check query.
  Excluding them keeps the index ~30-40 % smaller and makes scans faster.

Operational note:
  CREATE INDEX CONCURRENTLY cannot run inside a transaction. The upgrade and
  downgrade blocks are wrapped in op.get_context().autocommit_block() which
  commits any open transaction first and switches the connection to autocommit
  for the duration — the recommended Alembic pattern for this situation.

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2g3
Create Date: 2026-05-03 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2g3h4"
down_revision: Union[str, Sequence[str], None] = "h8c9d0e1f2g3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_NAME = "ix_appts_overlap_check"
_TABLE_NAME = "appointments"
_WHERE = "status NOT IN ('canceled', 'canceled_by_patient', 'no_show')"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX_NAME,
            _TABLE_NAME,
            ["doctor_id", "starts_at", "ends_at"],
            postgresql_where=sa.text(_WHERE),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX_NAME,
            table_name=_TABLE_NAME,
            postgresql_concurrently=True,
            if_exists=True,
        )
