"""
app/core/crypto.py
──────────────────────────────────────────────────────────────────────────────
AES-256-GCM encryption for sensitive patient fields.

Design decisions:
  • Each ciphertext is self-contained: base64( version_byte | iv | tag | ct )
    → No external IV table. One DB column, one atomic value.
  • `key_version` byte lets us rotate keys without a lock-table migration:
    - Old rows keep their key_version; new writes use the current key.
    - A background job re-encrypts stale rows at its own pace.
  • GCM tag (16 bytes) guarantees integrity — tampering raises DecryptionError,
    not silent garbage.
  • HMAC-SHA256 search hashes use a *separate* SEARCH_KEY so a leak of the
    encryption key doesn't expose the search index (and vice-versa).

Wire format (binary, then base64url-encoded):
  ┌─────────┬──────────────┬──────────────┬────────────┐
  │ 1 byte  │   12 bytes   │   16 bytes   │  N bytes   │
  │ version │  random IV   │   GCM tag    │ ciphertext │
  └─────────┴──────────────┴──────────────┴────────────┘
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

# ── Constants ─────────────────────────────────────────────────────────────────

IV_LEN:   Final[int] = 12   # 96-bit — GCM spec recommends this length
TAG_LEN:  Final[int] = 16   # 128-bit authentication tag
MAX_KEYS: Final[int] = 256  # version fits in 1 unsigned byte

# ── Exceptions ────────────────────────────────────────────────────────────────

class CryptoError(Exception):
    """Base for all crypto failures — never expose internals to HTTP layer."""

class EncryptionError(CryptoError):
    """Raised when encryption fails (bad key, oversized payload, etc.)."""

class DecryptionError(CryptoError):
    """
    Raised on tampered ciphertext, wrong key version, or malformed wire format.
    Callers should treat this as a 422 / audit event — never silently ignore.
    """

class UnknownKeyVersion(DecryptionError):
    """The stored key_version has no corresponding key in the keyring."""


# ── Key management ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _KeyEntry:
    version: int       # 0–255
    raw: bytes         # exactly 32 bytes for AES-256


@lru_cache(maxsize=1)
def _load_keyring() -> dict[int, _KeyEntry]:
    """
    Build the keyring from settings at startup (cached for the process lifetime).

    Settings format (comma-separated, ordered oldest→newest):
        ENCRYPTION_KEYS = "base64key0,base64key1,base64key2"

    The *last* entry is always the active (current) key used for new encryptions.
    All entries are kept for decryption of older rows.

    To rotate: append a new key to ENCRYPTION_KEYS and redeploy. The old key
    stays in the ring until all rows with its version are re-encrypted.
    """
    raw_keys: list[str] = [k.strip() for k in settings.ENCRYPTION_KEYS.split(",") if k.strip()]

    if not raw_keys:
        raise EncryptionError("ENCRYPTION_KEYS is empty — cannot start service.")
    if len(raw_keys) > MAX_KEYS:
        raise EncryptionError(f"Too many keys ({len(raw_keys)}); max is {MAX_KEYS}.")

    keyring: dict[int, _KeyEntry] = {}
    for version, b64 in enumerate(raw_keys):
        try:
            key_bytes = base64.b64decode(b64)
        except Exception as exc:
            raise EncryptionError(f"Key at version {version} is not valid base64.") from exc

        if len(key_bytes) != 32:
            raise EncryptionError(
                f"Key at version {version} must be 32 bytes (AES-256); "
                f"got {len(key_bytes)}."
            )
        keyring[version] = _KeyEntry(version=version, raw=key_bytes)

    return keyring


def _current_key() -> _KeyEntry:
    """Return the active key (highest version number)."""
    ring = _load_keyring()
    return ring[max(ring)]


def _key_for_version(version: int) -> _KeyEntry:
    ring = _load_keyring()
    try:
        return ring[version]
    except KeyError:
        raise UnknownKeyVersion(
            f"Key version {version} not found in keyring. "
            "Has the ENCRYPTION_KEYS setting been truncated?"
        )


# ── Core encrypt / decrypt ─────────────────────────────────────────────────────

def encrypt(plaintext: str) -> tuple[str, int]:
    """
    Encrypt *plaintext* with the current active key.

    Returns:
        (ciphertext_b64, key_version)

    The caller should persist both values. For SQLAlchemy models using
    EncryptedString TypeDecorator the version is embedded in the wire format,
    so the column value is self-contained; the `key_version` INT column on the
    row is an *index* that lets you find all rows needing re-encryption without
    scanning every ciphertext.

    Performance note: AESGCM object creation is cheap (just key scheduling).
    Each call generates a fresh cryptographically-random IV — never reuse IVs
    under the same key.
    """
    if not isinstance(plaintext, str):
        raise EncryptionError(f"encrypt() expects str, got {type(plaintext).__name__}.")

    entry = _current_key()
    iv = os.urandom(IV_LEN)

    try:
        aesgcm = AESGCM(entry.raw)
        # AESGCM.encrypt returns ct || tag (tag appended)
        ct_with_tag: bytes = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    except Exception as exc:
        raise EncryptionError("Encryption failed.") from exc

    # Wire format: version(1) | iv(12) | ct_with_tag(N+16)
    wire: bytes = struct.pack("B", entry.version) + iv + ct_with_tag
    return base64.b64encode(wire).decode("ascii"), entry.version


def decrypt(ciphertext_b64: str) -> str:
    """
    Decrypt a value produced by `encrypt()`.

    Raises DecryptionError on any failure — callers must not expose the reason
    to end-users (timing oracle / information leak).
    """
    if not isinstance(ciphertext_b64, str):
        raise DecryptionError("Expected base64 string.")

    try:
        wire = base64.b64decode(ciphertext_b64)
    except Exception as exc:
        raise DecryptionError("Ciphertext is not valid base64.") from exc

    min_len = 1 + IV_LEN + TAG_LEN  # version + iv + tag (empty plaintext)
    if len(wire) < min_len:
        raise DecryptionError(
            f"Ciphertext too short: {len(wire)} bytes (min {min_len})."
        )

    version = struct.unpack("B", wire[:1])[0]
    iv = wire[1 : 1 + IV_LEN]
    ct_with_tag = wire[1 + IV_LEN :]

    entry = _key_for_version(version)

    try:
        aesgcm = AESGCM(entry.raw)
        plaintext_bytes = aesgcm.decrypt(iv, ct_with_tag, None)
    except InvalidTag:
        # Distinguish tamper from key-mismatch for internal logging only
        raise DecryptionError(
            "GCM authentication tag mismatch — data may have been tampered with."
        )
    except Exception as exc:
        raise DecryptionError("Decryption failed.") from exc

    return plaintext_bytes.decode("utf-8")


# ── Search hash (HMAC-SHA256) ──────────────────────────────────────────────────

def make_search_hash(value: str) -> str:
    """
    Produce a deterministic HMAC-SHA256 hex digest for equality lookups.

    Usage:
        patient.name_search_hash = make_search_hash(patient_name.lower().strip())

    Then query:
        WHERE name_search_hash = :hash
          AND clinic_id         = :clinic_id   -- always scope to tenant!

    Security properties:
      • HMAC (not plain SHA256) prevents rainbow-table attacks on the hash index.
      • The SEARCH_HMAC_KEY is separate from encryption keys — a key leak in
        one subsystem does not compromise the other.
      • Normalise (lower + strip) before hashing so "Juan " == "juan".
      • Only exact-match is supported. For fuzzy search, consider a dedicated
        search service (Meilisearch, pg_trgm on a plaintext shadow table with
        row-level security).
    """
    if not isinstance(value, str):
        raise CryptoError(f"make_search_hash() expects str, got {type(value).__name__}.")

    key = settings.SEARCH_HMAC_KEY.encode("utf-8")
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


# ── Convenience: re-encrypt a single value to the current key ─────────────────

def reencrypt(ciphertext_b64: str) -> tuple[str, int]:
    """
    Decrypt then re-encrypt with the current active key.

    Used by the background key-rotation job:

        if record.key_version < current_version:
            record.field_enc, record.key_version = reencrypt(record.field_enc)

    Never call this in a hot path — it's O(rows) work done offline.
    """
    plaintext = decrypt(ciphertext_b64)
    return encrypt(plaintext)


# ── Key generation helper (run once, store result in secrets manager) ──────────

def generate_key_b64() -> str:
    """
    Generate a fresh AES-256 key encoded as base64.

    Run from a management command:
        python -c "from app.core.crypto import generate_key_b64; print(generate_key_b64())"

    Store the result in AWS Secrets Manager / Vault / .env — never in source.
    """
    return base64.b64encode(os.urandom(32)).decode("ascii")
