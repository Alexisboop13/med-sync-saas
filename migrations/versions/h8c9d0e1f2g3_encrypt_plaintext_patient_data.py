"""encrypt plaintext patient data

Encrypt existing plaintext values in full_name_enc and rebuild search_text.

Prior to this migration, full_name_enc stored raw plaintext because the
EncryptedString TypeDecorator was added to the model after the table was
populated.  Rows written after this migration have full ciphertext; rows
written before it still have plaintext, which causes process_result_value
to call decrypt() on a non-base64 value and raise DecryptionError.

Strategy:
  1. Stage current value in full_name_temp (prevents data loss if the UPDATE
     fails partway through).
  2. For each row, try decrypt().  If it succeeds the row is already
     encrypted (idempotent re-run), skip it.  If it raises, the value is
     plaintext — encrypt it and write back, together with search_text and
     key_version.
  3. Drop the staging column.

Downgrade is intentionally disabled: we cannot recover plaintext from
ciphertext without the key, and reverting to an unencrypted schema would
be a compliance regression.

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-05-03 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h8c9d0e1f2g3"
down_revision: Union[str, Sequence[str], None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # -- 1. Stage plaintext in temp column --------------------------------
    op.add_column("patients", sa.Column("full_name_temp", sa.Text(), nullable=True))
    conn.execute(sa.text("UPDATE patients SET full_name_temp = full_name_enc"))

    # -- 2. Encrypt each plaintext row ------------------------------------
    from app.core.crypto import DecryptionError, _current_key, decrypt, encrypt

    current_version = _current_key().version

    rows = conn.execute(
        sa.text(
            "SELECT id, full_name_temp "
            "FROM patients "
            "WHERE full_name_temp IS NOT NULL"
        )
    ).fetchall()

    for patient_id, value in rows:
        # Idempotency: if value is already valid ciphertext, skip it.
        try:
            decrypt(value)
            continue
        except Exception:
            pass  # plaintext — fall through to encrypt

        ciphertext, _ = encrypt(value)
        conn.execute(
            sa.text(
                "UPDATE patients "
                "SET full_name_enc = :enc, "
                "    search_text   = :st, "
                "    key_version   = :kv "
                "WHERE id = :id"
            ),
            {
                "enc": ciphertext,
                "st": value.lower(),
                "kv": current_version,
                "id": str(patient_id),
            },
        )

    # -- 3. Drop staging column ------------------------------------------
    op.drop_column("patients", "full_name_temp")


def downgrade() -> None:
    raise NotImplementedError(
        "This migration is irreversible: plaintext cannot be recovered "
        "from ciphertext after the original values have been overwritten."
    )
