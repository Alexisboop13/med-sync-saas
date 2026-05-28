from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.appointment import Appointment
    from app.models.user import User


class RescheduleRequestStatus(StrEnum):
    PENDING  = "pending"
    RESOLVED = "resolved"
    IGNORED  = "ignored"


class RescheduleRequest(TenantBase):
    __tablename__ = "reschedule_requests"

    appointment_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    patient_note: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RescheduleRequestStatus.PENDING,
        server_default="pending",
        index=True,
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    appointment: Mapped["Appointment"] = relationship(
        "Appointment",
        back_populates="reschedule_requests",
        lazy="select",
    )

    resolved_by: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[resolved_by_id],
        lazy="select",
    )

    __table_args__ = (
        Index("ix_reschedule_requests_clinic_status", "clinic_id", "status"),
    )

    def __repr__(self) -> str:
        return (
            f"<RescheduleRequest id={self.id} appointment={self.appointment_id} "
            f"status={self.status}>"
        )
