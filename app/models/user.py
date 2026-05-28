"""
app/models/user.py
──────────────────────────────────────────────────────────────────────────────
User — staff member belonging to a clinic.

Role model (RBAC):
  OWNER      Full access. Manages billing, users, clinic settings.
             Automatically satisfies every role gate in require_role().
  DOCTOR     Manages own patients and medical records.
             Cannot access billing or other doctors' private records.
  ASSISTANT  Read/write on Agenda (appointments) and confirmations only.
             Cannot read medical records or patient PII beyond name/phone.

One User may be linked to a Doctor profile (via the `doctor` back-ref) when
role == DOCTOR. An OWNER or ASSISTANT never has a Doctor row.

Password security:
  `hashed_password` stores a bcrypt/argon2 hash — NEVER the plaintext.
  The hashing happens in the auth service layer (not in this model).
  The `set_password()` helper is intentionally absent here to keep the model
  as a pure data layer; password logic lives in app/services/auth_service.py.

Email storage:
  `email_enc` is encrypted via EncryptedString for HIPAA/NOM-024 compliance.
  `email_search_hash` (HMAC-SHA256) allows fast exact-match lookups for login
  without decrypting every row.
  Login flow: hash(input_email) → WHERE email_search_hash = ? AND clinic_id = ?
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import EncryptedString
from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.doctor import Doctor
    from app.models.audit_log import AuditLog
    from app.models.appointment import Appointment


# ── Role enum ─────────────────────────────────────────────────────────────────

class Role(StrEnum):
    OWNER = "owner"
    DOCTOR = "doctor"
    ASSISTANT = "assistant"


# ── Model ─────────────────────────────────────────────────────────────────────

class User(TenantBase):
    """
    Staff member of a clinic. Always scoped to one clinic via TenantBase.

    Unique constraints are COMPOSITE (clinic_id + email_search_hash) because
    the same email address may legitimately belong to staff in two different
    clinics — the tenancy is the disambiguation key.
    """

    __tablename__ = "users"

    # ── Identity ──────────────────────────────────────────────────────────────
    email_enc: Mapped[str] = mapped_column(
        EncryptedString,
        nullable=False,
        comment="AES-256-GCM encrypted email. Never query this column directly.",
    )

    email_search_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment=(
            "HMAC-SHA256(lower(email), SEARCH_HMAC_KEY). "
            "Used for login lookup: WHERE clinic_id=? AND email_search_hash=?"
        ),
    )

    full_name_enc: Mapped[str] = mapped_column(
        EncryptedString,
        nullable=False,
        comment="Encrypted display name. Decrypted only when rendering UI.",
    )

    phone_enc: Mapped[Optional[str]] = mapped_column(
        EncryptedString,
        nullable=True,
        comment="Encrypted contact phone. Decrypted only at the API layer.",
    )

    # ── Auth ──────────────────────────────────────────────────────────────────
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment=(
            "Argon2id hash produced by passlib. "
            "Format: $argon2id$v=19$m=65536,t=2,p=1$..."
        ),
    )

    # ── RBAC ──────────────────────────────────────────────────────────────────
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=Role.ASSISTANT,
        server_default="assistant",
        index=True,
        comment="One of: owner, doctor, assistant. Enforced by require_role() in deps.py.",
    )

    # ── Status ────────────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment="Soft-disable without deleting. get_current_user() rejects inactive users.",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    clinic: Mapped["Clinic"] = relationship(
        "Clinic",
        back_populates="users",
        lazy="select",
    )

    # One-to-one: User ↔ Doctor (only set when role == DOCTOR)
    doctor: Mapped[Optional["Doctor"]] = relationship(
        "Doctor",
        back_populates="user",
        uselist=False,          # one user = at most one doctor profile
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # Appointments created by this user (regardless of role)
    created_appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment",
        foreign_keys="[Appointment.created_by_id]",
        back_populates="created_by",
        lazy="select",
    )

    audit_events: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        foreign_keys="[AuditLog.actor_id]",
        back_populates="actor",
        lazy="select",
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        # Primary login lookup: hash(email) scoped to clinic
        Index(
            "ix_users_clinic_email_hash",
            "clinic_id",
            "email_search_hash",
            unique=True,   # same email cannot appear twice within one clinic
        ),
        # Role filter for admin user-management endpoints
        Index("ix_users_clinic_role", "clinic_id", "role"),
    )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @property
    def is_owner(self) -> bool:
        return self.role == Role.OWNER

    @property
    def is_doctor(self) -> bool:
        return self.role == Role.DOCTOR

    @property
    def is_assistant(self) -> bool:
        return self.role == Role.ASSISTANT

    def __repr__(self) -> str:
        return f"<User id={self.id} role={self.role} clinic={self.clinic_id}>"
