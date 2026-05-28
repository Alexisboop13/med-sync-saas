"""
app/db/types.py
──────────────────────────────────────────────────────────────────────────────
Custom SQLAlchemy column types for transparent field-level encryption.

How it works:
  SQLAlchemy calls `process_bind_param`  before  writing to the DB.
  SQLAlchemy calls `process_result_value` after reading  from the DB.

  The ORM model layer never sees ciphertext — it always works with plaintext
  strings. The crypto boundary is exactly here, in these two methods.

  ┌──────────────┐   process_bind_param    ┌──────────────────────┐
  │ Python model │  ──── encrypt() ──────► │  PostgreSQL TEXT col  │
  │  (plaintext) │  ◄─── decrypt() ─────   │  (base64 ciphertext) │
  └──────────────┘   process_result_value  └──────────────────────┘

Performance profile:
  • Encrypt: ~5–15 µs per field (AES-GCM on modern hardware).
  • Decrypt: same order of magnitude.
  • For a patient row with 5 encrypted fields: ~50–75 µs overhead total.
  • Analytics queries that filter on `clinic_id`, `status`, `starts_at` are
    NOT affected — those columns are stored in plaintext.

Usage in models:
    from app.db.types import EncryptedString, SearchableEncryptedString

    class Patient(TenantBase):
        name_enc: Mapped[str] = mapped_column(EncryptedString)
        name_search_hash: Mapped[str] = mapped_column(String(64), index=True)
        phone_enc: Mapped[Optional[str]] = mapped_column(EncryptedString, nullable=True)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.crypto import decrypt, encrypt, DecryptionError, EncryptionError


# ── EncryptedString ────────────────────────────────────────────────────────────

class EncryptedString(TypeDecorator):
    """
    Transparent AES-256-GCM encryption for string columns.

    Stored as TEXT in PostgreSQL (base64-encoded wire format).
    The wire format embeds the key version, so no extra column is needed
    for decryption — the `key_version` INT on the *row* is only used as an
    index for the background rotation job.

    Null semantics:
      • None in Python  →  NULL in DB   (no encryption of null)
      • ""  in Python   →  encrypted empty string in DB

    Why TypeDecorator instead of a hybrid property?
      TypeDecorator works transparently with bulk inserts, upserts, and
      SQLAlchemy Core expressions. A hybrid property would require explicit
      call sites in every service method.
    """

    impl = Text          # Underlying Postgres type
    cache_ok = True      # Values are deterministically derived from input

    # ── Write path (Python → DB) ───────────────────────────────────────────

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        """Called before INSERT / UPDATE. Encrypts plaintext → ciphertext."""
        if value is None:
            return None

        if not isinstance(value, str):
            # Coerce numeric or other types gracefully; model validators
            # should have already rejected bad types before we get here.
            value = str(value)

        try:
            ciphertext, _version = encrypt(value)
            return ciphertext
        except EncryptionError as exc:
            # Re-raise as a ValueError so SQLAlchemy surfaces it correctly
            # without leaking crypto internals in the message.
            raise ValueError("Failed to encrypt column value.") from exc

    # ── Read path (DB → Python) ────────────────────────────────────────────

    def process_result_value(self, value: Any, dialect: Dialect) -> str | None:
        """Called after SELECT. Decrypts ciphertext → plaintext."""
        if value is None:
            return None

        try:
            return decrypt(str(value))
        except DecryptionError as exc:
            # In production, log this as a CRITICAL audit event and re-raise.
            # Never return partial / garbage data to the application layer.
            raise ValueError(
                "Failed to decrypt column value. The row may be corrupted or "
                "the encryption key may have been rotated without migrating this row."
            ) from exc

    # ── Reflection / comparison helpers ───────────────────────────────────

    def copy(self, **kwargs: Any) -> "EncryptedString":
        return EncryptedString()

    def __repr__(self) -> str:
        return "EncryptedString()"


# ── NullableEncryptedString ────────────────────────────────────────────────────

class NullableEncryptedString(EncryptedString):
    """
    Variant that also encrypts empty strings as NULL.
    Useful for optional demographic fields (address, secondary phone, etc.)
    where you want `None` and `""` to be treated identically at the DB level.
    """

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        if not value:   # None or ""
            return None
        return super().process_bind_param(value, dialect)

    def copy(self, **kwargs: Any) -> "NullableEncryptedString":
        return NullableEncryptedString()


# ── EncryptedJSON ──────────────────────────────────────────────────────────────

class EncryptedJSON(TypeDecorator):
    """
    Encrypt an arbitrary JSON-serialisable object as a single TEXT column.

    Use for structured sensitive data (e.g. emergency contact nested object)
    where you don't want individual columns but still need encryption.

    Do NOT use for data you need to query inside Postgres (jsonb operators won't
    work on encrypted blobs). Use plain JSONB for non-sensitive structured data.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        import json
        if value is None:
            return None
        try:
            serialised = json.dumps(value, ensure_ascii=False)
            ciphertext, _ = encrypt(serialised)
            return ciphertext
        except (EncryptionError, TypeError) as exc:
            raise ValueError("Failed to encrypt JSON column value.") from exc

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        import json
        if value is None:
            return None
        try:
            plaintext = decrypt(str(value))
            return json.loads(plaintext)
        except (DecryptionError, ValueError) as exc:
            raise ValueError("Failed to decrypt JSON column value.") from exc

    def copy(self, **kwargs: Any) -> "EncryptedJSON":
        return EncryptedJSON()
