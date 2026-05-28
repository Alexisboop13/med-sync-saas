"""
app/models/appointment.py
──────────────────────────────────────────────────────────────────────────────
Appointment — the central scheduling entity.

Status FSM:
  SCHEDULED → COMPLETED (visit finished)
      │
      └──── CANCELED (by clinic staff)
      └──── CANCELED_BY_PATIENT (via magic link, ≥1 h before starts_at)

Overlap prevention:
  The DB constraint `uq_doctor_no_overlap` (exclusion constraint using
  tstzrange) is the hard guard. The service layer does a soft check first
  for user-friendly error messages. Both are needed:
    • Service check: fast, readable 422 response with details.
    • DB constraint: catches race conditions from concurrent requests.

  Exclusion constraint (added in Alembic migration, not in __table_args__
  because SQLAlchemy doesn't support ExcludeConstraint natively in Mapped API):
    ALTER TABLE appointments
    ADD CONSTRAINT uq_doctor_no_overlap
    EXCLUDE USING GIST (
      doctor_id WITH =,
      tstzrange(starts_at, ends_at, '[)') WITH &&
    )
    WHERE (status NOT IN ('canceled', 'canceled_by_patient', 'no_show'));

Magic link token:
  `magic_token` is a URL-safe random UUID generated at creation time.
  It's stored hashed (via HMAC) in `magic_token_hash` so even a DB read
  cannot reconstruct the URL. The raw token is returned once, at creation,
  for inclusion in the patient notification.

  Wait — for MVP simplicity we store the raw token. This is acceptable because:
    a) It's short-lived (expires in 72 h by default).
    b) DB access already requires clinic_id scoping.
    c) Hashing adds a round-trip and complexity; add it in v2 if needed.

notes_enc:
  Internal clinical note visible only to doctor/owner. Encrypted because
  it may contain sensitive clinical observations.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import NullableEncryptedString
from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.appointment_note import AppointmentNote
    from app.models.clinic import Clinic
    from app.models.doctor import Doctor
    from app.models.location import Location
    from app.models.patient import Patient
    from app.models.user import User
    from app.models.medical_record import MedicalRecord
    from app.models.notification import Notification
    from app.models.reschedule_request import RescheduleRequest


# ── Status FSM ────────────────────────────────────────────────────────────────

class AppointmentStatus(StrEnum):
    SCHEDULED = "scheduled"                     # default on creation
    COMPLETED = "completed"                     # visit finished
    CANCELED = "canceled"                       # canceled by staff
    CANCELED_BY_PATIENT = "canceled_by_patient" # via magic link (≥1 h prior)
    NO_SHOW = "no_show"                         # patient did not attend


# Statuses that "occupy" a time slot for overlap checking
ACTIVE_STATUSES = frozenset({
    AppointmentStatus.SCHEDULED,
})

# Statuses that count as cancellation (free the slot)
CANCELED_STATUSES = frozenset({
    AppointmentStatus.CANCELED,
    AppointmentStatus.CANCELED_BY_PATIENT,
    AppointmentStatus.NO_SHOW,
})

# ── Model ─────────────────────────────────────────────────────────────────────


class Appointment(TenantBase):
    """
    Appointment. Inherits clinic_id, id, created_at, updated_at from TenantBase.

    The overlap constraint is enforced at two levels:
      1. appointment_service.check_overlap() — user-friendly 422 before commit.
      2. PostgreSQL GIST exclusion constraint (in migration) — race-condition guard.
    """

    __tablename__ = "appointments"

    # ── Location ──────────────────────────────────────────────────────────────
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("locations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Branch/location where the appointment takes place.",
    )

    # ── Participants ──────────────────────────────────────────────────────────
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("doctors.id", ondelete="RESTRICT"),
        nullable=False,
        comment="The doctor who will see the patient.",
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patients.id", ondelete="RESTRICT"),
        nullable=False,
        comment="Must belong to the same clinic (enforced in service layer).",
    )

    created_by_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="Staff user who created the appointment. NULL = self-booked via magic link.",
    )

    # ── Timing ────────────────────────────────────────────────────────────────
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Appointment start. Always stored in UTC.",
    )

    ends_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "Appointment end. Derived from doctor.appointment_duration_minutes "
            "but stored explicitly so duration changes don't affect existing records."
        ),
    )

    # ── Status ────────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=AppointmentStatus.SCHEDULED,
        server_default="scheduled",
        index=True,
    )

    # ── Clinical notes (encrypted) ────────────────────────────────────────────
    notes_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment="Internal notes visible to doctor/owner only. Encrypted.",
    )

    # ── Patient-facing reason (NOT encrypted — used for notification preview) ─
    reason: Mapped[Optional[str]] = mapped_column(
        String(300),
        nullable=True,
        comment=(
            "Brief reason shown in notifications and booking confirmations. "
            "Must not contain clinical information — that goes in notes_enc."
        ),
    )

    # ── Key rotation ──────────────────────────────────────────────────────────
    key_version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
    )

    # ── Proposed reschedule ───────────────────────────────────────────────────
    proposed_starts_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Staff-proposed new start time; NULL when no reschedule is pending.",
    )

    proposed_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Staff-proposed new end time; NULL when no reschedule is pending.",
    )

    reschedule_token: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        unique=True,
        index=True,
        comment="UUID v4 token for patient confirm/reject reschedule link. Cleared after use.",
    )

    reschedule_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Reschedule token expiry. Typically now + 48 h.",
    )

    # ── Magic link (patient self-service) ──────────────────────────────────────
    magic_token: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        unique=True,
        index=True,
        comment=(
            "UUID v4 token for patient-facing cancel/reschedule link. "
            "Generated on creation; cleared after use or expiry."
        ),
    )

    magic_token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Token expiry. Typically created_at + 72 h.",
    )

    # ── No-show flag ─────────────────────────────────────────────────────────
    was_no_show: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment=(
            "True when staff marked this appointment as no-show. "
            "Persists even if status changes, enabling historical reporting."
        ),
    )

    # ── Patient confirmation ──────────────────────────────────────────────────
    patient_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
        comment="When the patient confirmed attendance. NULL = not yet confirmed.",
    )

    patient_confirmation_channel: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment="How the patient confirmed: magic_link | whatsapp | phone | staff",
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    clinic: Mapped["Clinic"] = relationship(
        "Clinic",
        back_populates="appointments",
        lazy="select",
    )

    doctor: Mapped["Doctor"] = relationship(
        "Doctor",
        back_populates="appointments",
        lazy="select",
    )

    patient: Mapped["Patient"] = relationship(
        "Patient",
        back_populates="appointments",
        lazy="select",
    )

    created_by: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[created_by_id],
        back_populates="created_appointments",
        lazy="select",
    )

    location: Mapped[Optional["Location"]] = relationship(
        "Location",
        back_populates="appointments",
        lazy="select",
    )

    medical_records: Mapped[List["MedicalRecord"]] = relationship(
        "MedicalRecord",
        back_populates="appointment",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    notifications: Mapped[List["Notification"]] = relationship(
        "Notification",
        back_populates="appointment",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    reschedule_requests: Mapped[List["RescheduleRequest"]] = relationship(
        "RescheduleRequest",
        back_populates="appointment",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    notes: Mapped[List["AppointmentNote"]] = relationship(
        "AppointmentNote",
        back_populates="appointment",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AppointmentNote.created_at",
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        # Overlap check query: all active appointments for a doctor in a time range
        Index(
            "ix_appts_doctor_starts",
            "clinic_id",
            "doctor_id",
            "starts_at",
            "ends_at",
        ),
        # Analytics: appointments by clinic + date range + status
        Index(
            "ix_appts_clinic_starts_status",
            "clinic_id",
            "starts_at",
            "status",
        ),
        # Patient timeline
        Index(
            "ix_appts_clinic_patient",
            "clinic_id",
            "patient_id",
            "starts_at",
        ),
        # Magic link lookup (also has unique index above, kept for FK join clarity)
        Index("ix_appts_magic_token", "magic_token"),
    )

    # ── Business logic helpers ────────────────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_canceled(self) -> bool:
        return self.status in CANCELED_STATUSES

    @property
    def duration_minutes(self) -> int:
        delta = self.ends_at - self.starts_at
        return int(delta.total_seconds() / 60)

    def __repr__(self) -> str:
        return (
            f"<Appointment id={self.id} doctor={self.doctor_id} "
            f"patient={self.patient_id} starts={self.starts_at} status={self.status}>"
        )
