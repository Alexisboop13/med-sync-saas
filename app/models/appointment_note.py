from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.appointment import Appointment
    from app.models.user import User


class AppointmentNote(TenantBase):
    """
    Clinical note attached to an appointment.
    Each note is a separate row — full history is preserved.
    Deletes are physical (the author or an owner/assistant can remove a note).
    """

    __tablename__ = "appointment_notes"

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    appointment: Mapped["Appointment"] = relationship(
        "Appointment",
        back_populates="notes",
        lazy="select",
    )

    created_by: Mapped[Optional["User"]] = relationship(
        "User",
        lazy="select",
    )

    __table_args__ = (
        Index("ix_appointment_notes_clinic_appt", "clinic_id", "appointment_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<AppointmentNote id={self.id} appointment={self.appointment_id} "
            f"author={self.created_by_id}>"
        )
