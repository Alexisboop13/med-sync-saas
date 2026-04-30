"""
app/models/patient.py
──────────────────────────────────────────────────────────────────────────────
Patient — the core PII-bearing entity; all personal fields are encrypted.

Encryption strategy recap (see crypto.py for full rationale):

  Stored encrypted (EncryptedString):
    full_name, phone, email, date_of_birth, address, emergency_contact_name,
    emergency_contact_phone, blood_type, allergies_enc, notes_enc

  Stored as HMAC hash (searchable, never decryptable to original):
    full_name_search_hash  ← make_search_hash(full_name.lower().strip())
    phone_search_hash      ← make_search_hash(phone digits only)

  Stored in plaintext (needed for queries / never PII):
    id, clinic_id, key_version, is_active, created_at, updated_at

key_version:
  Tracks which encryption key (version) was used for this row.
  The background key-rotation job does:
    SELECT * FROM patients WHERE key_version < current_version LIMIT 500;
    → reencrypt each field → UPDATE key_version
  This allows zero-downtime key rotation.

NOM-024 / HIPAA field mapping:
  full_name            ← Nombre del paciente
  date_of_birth        ← Fecha de nacimiento
  blood_type           ← Tipo de sangre
  allergies_enc        ← Alergias conocidas (free-text JSONB encrypted)
  notes_enc            ← Notas clínicas generales (no es expediente — ver MedicalRecord)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import EncryptedString, NullableEncryptedString, EncryptedJSON
from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.appointment import Appointment
    from app.models.medical_record import MedicalRecord


class Patient(TenantBase):
    """
    Patient. Inherits clinic_id, id, created_at, updated_at from TenantBase.

    Every _enc column is transparently encrypted/decrypted by the TypeDecorator
    in db/types.py — the service and API layers work with plaintext strings.

    NEVER add an unencrypted copy of a PII field "for convenience".
    If you need to search by a field, add a corresponding _search_hash column.
    """

    __tablename__ = "patients"

    # ── Core identity (encrypted) ─────────────────────────────────────────────
    full_name_enc: Mapped[str] = mapped_column(
        EncryptedString,
        nullable=False,
        comment="Encrypted full legal name.",
    )

    full_name_search_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment=(
            "HMAC-SHA256(lower(full_name.strip()), SEARCH_HMAC_KEY). "
            "Exact-match search: WHERE clinic_id=? AND full_name_search_hash=?"
        ),
    )

    # ── Contact (encrypted) ───────────────────────────────────────────────────
    phone_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Encrypted phone number. Normalise to E.164 before encrypting.",
    )

    phone_search_hash: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        comment="HMAC-SHA256 of digits-only phone. Allows lookup by phone number.",
    )

    email_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Encrypted email. Used only for notification sending.",
    )

    # ── Demographics (encrypted) ──────────────────────────────────────────────
    date_of_birth_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Encrypted ISO-8601 date string: '1990-04-15'.",
    )

    gender_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Encrypted. Values: male, female, non_binary, prefer_not_to_say.",
    )

    address_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Encrypted full address (free text).",
    )

    # ── Medical background (encrypted) ────────────────────────────────────────
    blood_type_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Encrypted. Values: A+, A-, B+, B-, AB+, AB-, O+, O-.",
    )

    allergies_enc: Mapped[Optional[dict]] = mapped_column(
        EncryptedJSON,
        nullable=True,
        comment=(
            "Encrypted JSONB. Schema: "
            '{"medications": ["penicillin"], "foods": [], "environmental": []}. '
            "Stored as JSON so it can be extended without schema migration."
        ),
    )

    notes_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment=(
            "General intake notes visible to all clinic staff. "
            "Per-visit clinical notes belong in MedicalRecord, not here."
        ),
    )

    # ── Emergency contact (encrypted) ─────────────────────────────────────────
    emergency_contact_name_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
    )

    emergency_contact_phone_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
    )

    # ── Key rotation tracking ─────────────────────────────────────────────────
    key_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
        index=True,
        comment=(
            "Index of the encryption key used for this row. "
            "Background job: UPDATE patients SET ... WHERE key_version < :current"
        ),
    )

    # ── Soft delete ───────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="False = soft-deleted. Keeps audit trail and medical records intact.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    clinic: Mapped["Clinic"] = relationship(
        "Clinic",
        back_populates="patients",
        lazy="select",
    )

    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment",
        back_populates="patient",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    medical_records: Mapped[List["MedicalRecord"]] = relationship(
        "MedicalRecord",
        back_populates="patient",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        # Exact-match search by name (most common patient lookup)
        Index(
            "ix_patients_clinic_name_hash",
            "clinic_id",
            "full_name_search_hash",
        ),
        # Lookup by phone
        Index(
            "ix_patients_clinic_phone_hash",
            "clinic_id",
            "phone_search_hash",
        ),
        # Key-rotation job: find rows needing re-encryption
        Index(
            "ix_patients_clinic_keyver",
            "clinic_id",
            "key_version",
        ),
        # Soft-delete filter (list active patients)
        Index(
            "ix_patients_clinic_active",
            "clinic_id",
            "is_active",
        ),
    )

    def __repr__(self) -> str:
        return f"<Patient id={self.id} clinic={self.clinic_id} key_v={self.key_version}>"
