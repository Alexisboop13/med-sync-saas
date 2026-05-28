"""
app/models/doctor.py
──────────────────────────────────────────────────────────────────────────────
Doctor — professional profile linked 1-to-1 to a User with role == DOCTOR.

Separation of concerns:
  User   → authentication, role, contact info (encrypted)
  Doctor → schedule, specialty, appointment rules

This separation means:
  • An OWNER can look at all doctors without loading auth fields.
  • The `working_hours` JSONB is easy to query/patch without touching PII.
  • Future: a doctor may be temporarily deactivated (is_accepting_patients=False)
    without deactivating their User login.

working_hours JSONB schema:
  {
    "mon": [{"start": "09:00", "end": "13:00"}, {"start": "15:00", "end": "19:00"}],
    "tue": [{"start": "09:00", "end": "14:00"}],
    "wed": [],          ← day off
    "thu": [...],
    "fri": [...],
    "sat": [],
    "sun": []
  }
  Validated by Pydantic schema at the API layer before persisting.
  Stored as JSONB so the magic-link availability endpoint can do:
    SELECT working_hours->'mon' FROM doctors WHERE id = ?
  without pulling the full row.

appointment_duration_minutes:
  Default slot duration used by the overlap-check service.
  The Appointment model stores the exact `ends_at`, but this default drives
  the patient-facing booking UI slot grid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.appointment import Appointment
    from app.models.medical_record import MedicalRecord


class Doctor(TenantBase):
    """
    Doctor profile. Inherits clinic_id, id, created_at, updated_at from TenantBase.
    """

    __tablename__ = "doctors"

    # ── Link to User ──────────────────────────────────────────────────────────
    user_id: Mapped[PG_UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,          # 1-to-1: one user → at most one doctor profile
        index=True,
    )

    # ── Professional info ─────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="Dr.",
        server_default="Dr.",
        comment="Honorific title displayed before the name (Dr., Dra., Lic., etc.).",
    )

    specialty: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
        default="General Practice",
        comment="Displayed on patient-facing booking page.",
    )

    bio: Mapped[Optional[str]] = mapped_column(
        String(1000),
        nullable=True,
        comment="Short public bio for the booking page.",
    )

    avatar_s3_key: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="S3 key for profile photo, served via presigned URL.",
    )

    # ── Schedule ──────────────────────────────────────────────────────────────
    working_hours: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Weekly schedule as JSONB. Schema: "
            "{day: [{start: 'HH:MM', end: 'HH:MM'}]}. "
            "Empty list means day off."
        ),
    )

    appointment_duration_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=30,
        server_default="30",
        comment="Default slot length used by the booking grid UI.",
    )

    # ── Availability flags ────────────────────────────────────────────────────
    is_accepting_patients: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        comment=(
            "False hides this doctor from the magic-link booking page. "
            "Useful for sabbaticals, maternity leave, etc."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped["User"] = relationship(
        "User",
        back_populates="doctor",
        lazy="select",
    )

    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment",
        back_populates="doctor",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    medical_records: Mapped[List["MedicalRecord"]] = relationship(
        "MedicalRecord",
        back_populates="doctor",
        lazy="select",
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        # Fast lookup of available doctors per clinic for booking page
        Index(
            "ix_doctors_clinic_accepting",
            "clinic_id",
            "is_accepting_patients",
        ),
        Index("ix_doctors_user_id", "user_id"),   # explicit for FK joins
    )

    def __repr__(self) -> str:
        return (
            f"<Doctor id={self.id} specialty={self.specialty!r} "
            f"clinic={self.clinic_id}>"
        )
