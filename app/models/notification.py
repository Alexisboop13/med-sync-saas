"""
app/models/notification.py
──────────────────────────────────────────────────────────────────────────────
Notification — delivery record for every outbound communication.

Channels:
  EMAIL      SMTP (SendGrid / SES). Sent by notification_service.send_email().
  WHATSAPP   Not a direct API send — generates a wa.me deep-link for staff
             to click. Logged here for audit purposes.
  SMS        Future channel (Twilio); schema supports it already.

Why log WhatsApp "sends"?
  The WhatsApp Business API has strict opt-in requirements. For the MVP we
  generate pre-filled wa.me links that the doctor/assistant clicks manually.
  Logging the link generation (not the delivery) gives us the audit trail
  needed to prove the clinic communicated with the patient.

Recipient masking:
  `recipient_enc` stores the encrypted email/phone so we can retry delivery
  without re-fetching the patient row. Stored encrypted because an email or
  phone number in a notification queue is still PII.

Retry logic:
  The notification_service handles retries (max 3 attempts with exponential
  back-off). Each attempt increments `attempt_count`. Status transitions:
    PENDING → SENT (success on any attempt)
    PENDING → FAILED (attempt_count >= 3 and still failing)

metadata JSONB:
  Flexible per-channel envelope:
    EMAIL:      { "subject": "...", "template": "appointment_reminder",
                  "sendgrid_msg_id": "...", "error": null }
    WHATSAPP:   { "wa_link": "https://wa.me/...", "message_preview": "..." }
    SMS:        { "twilio_sid": "...", "error": null }
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, SmallInteger, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.types import NullableEncryptedString
from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.appointment import Appointment


# ── Enums ─────────────────────────────────────────────────────────────────────

class NotificationChannel(StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"    # patient opted out / no contact info


class NotificationType(StrEnum):
    APPOINTMENT_CONFIRMATION = "appointment_confirmation"
    APPOINTMENT_REMINDER = "appointment_reminder"      # sent 24 h before
    APPOINTMENT_CANCELED = "appointment_canceled"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    MAGIC_LINK = "magic_link"                # cancel/reschedule link
    PRESCRIPTION_READY = "prescription_ready"


# ── Model ─────────────────────────────────────────────────────────────────────

class Notification(TenantBase):
    """
    One row per outbound notification attempt batch.
    Inherits clinic_id, id, created_at, updated_at from TenantBase.
    """

    __tablename__ = "notifications"

    # ── Link to appointment (optional — some notifications are standalone) ───
    appointment_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # ── Classification ────────────────────────────────────────────────────────
    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="email | whatsapp | sms",
    )

    notification_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Drives template selection in notification_service.py.",
    )

    # ── Recipient (encrypted) ─────────────────────────────────────────────────
    recipient_enc: Mapped[Optional[str]] = mapped_column(
        NullableEncryptedString,
        nullable=True,
        comment=(
            "Encrypted email address or E.164 phone number. "
            "Kept here so retry doesn't need to re-join Patient."
        ),
    )

    # ── Delivery status ───────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=NotificationStatus.PENDING,
        server_default="pending",
        index=True,
    )

    attempt_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=0,
        server_default="0",
        comment="Incremented on each delivery attempt.",
    )

    sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="Set on first successful delivery.",
    )

    # ── Per-channel metadata (JSONB — not encrypted, no PII values) ──────────
    extra_data: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Channel-specific data. Must NOT contain PII values. "
            "EMAIL: {subject, template, provider_msg_id, error}. "
            "WHATSAPP: {wa_link, message_preview}."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    appointment: Mapped[Optional["Appointment"]] = relationship(
        "Appointment",
        back_populates="notifications",
        lazy="select",
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        # Retry job: find pending notifications older than N minutes
        Index(
            "ix_notifs_clinic_status_created",
            "clinic_id",
            "status",
            "created_at",
        ),
        # Notification history for an appointment
        Index(
            "ix_notifs_appt_channel",
            "appointment_id",
            "channel",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} channel={self.channel} "
            f"type={self.notification_type} status={self.status}>"
        )
