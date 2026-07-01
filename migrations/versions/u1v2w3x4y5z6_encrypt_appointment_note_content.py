"""encrypt appointment note content

Replaces the plaintext `content` column on appointment_notes with
`content_enc` (EncryptedString), per the CLAUDE.md rule that clinical notes
must be encrypted at rest.

Strategy:
  1. Add content_enc (nullable, staging).
  2. Encrypt every existing row's content into content_enc.
  3. Make content_enc NOT NULL.
  4. Drop the old plaintext content column.

Downgrade decrypts content_enc back into a plaintext content column and
drops content_enc — safe because the encryption key material still exists
at downgrade time (unlike a same-column overwrite migration).

Revision ID: u1v2w3x4y5z6
Revises: t0u1v2w3x4y5
Create Date: 2026-06-30 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, Sequence[str], None] = "t0u1v2w3x4y5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "appointment_notes"


def upgrade() -> None:
    conn = op.get_bind()

    op.add_column(_TABLE, sa.Column("content_enc", sa.Text(), nullable=True))

    from app.core.crypto import encrypt

    rows = conn.execute(sa.text(f"SELECT id, content FROM {_TABLE}")).fetchall()
    for note_id, plaintext in rows:
        ciphertext, _version = encrypt(plaintext)
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET content_enc = :enc WHERE id = :id"),
            {"enc": ciphertext, "id": str(note_id)},
        )

    op.alter_column(_TABLE, "content_enc", nullable=False)
    op.drop_column(_TABLE, "content")


def downgrade() -> None:
    conn = op.get_bind()

    op.add_column(_TABLE, sa.Column("content", sa.Text(), nullable=True))

    from app.core.crypto import decrypt

    rows = conn.execute(sa.text(f"SELECT id, content_enc FROM {_TABLE}")).fetchall()
    for note_id, ciphertext in rows:
        plaintext = decrypt(ciphertext)
        conn.execute(
            sa.text(f"UPDATE {_TABLE} SET content = :val WHERE id = :id"),
            {"val": plaintext, "id": str(note_id)},
        )

    op.alter_column(_TABLE, "content", nullable=False)
    op.drop_column(_TABLE, "content_enc")
