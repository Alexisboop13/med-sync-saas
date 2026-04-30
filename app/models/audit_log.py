"""
app/models/audit_log.py
──────────────────────────────────────────────────────────────────────────────
AuditLog — immutable event ledger for compliance and analytics.

Design: Event-Driven / Append-Only
  • Rows are NEVER updated or deleted (enforced at service layer + DB trigger).
  • `payload` is JSONB (not EncryptedJSON) because analytics queries need to
    do jsonb_path_query(), GIN index lookups, and ETL extracts directly on Postgres.
    Sensitive field values in payload are ALWAYS masked before write (see below).
  • Schema follows CloudEvents 1.0 for future Kafka/Kinesis compatibility.

Payload schema (CloudEvents-compatible JSONB):
  {
    "specversion": "1.0",
    "id":          "<audit_log.id>",
    "source":      "/api/v1/patients",        ← request path
    "type":        "patient.record.viewed",   ← dot-notation event type
    "subject":     "<entity_id>",             ← the affected record's UUID
    "time":        "2025-06-01T12:00:00Z",
    "datacontenttype": "application/json",
    "data": {
      "actor_role":      "doctor",
      "fields_accessed": ["diagnosis_enc"],   ← field names, never values
      "ip_address":      "192.168.1.1",
      "user_agent":      "Mozilla/5.0...",
      "before":          { "status": "scheduled" },   ← plaintext safe fields only
      "after":           { "status": "confirmed"  },
      "masked_fields":   ["name_enc", "phone_enc"]    ← listed but not shown
    }
  }

PII masking rule:
  Any field ending in `_enc` MUST be listed in `data.masked_fields` and
  NEVER included in `data.before` or `data.after`. The audit system records
  WHAT changed (field name), not WHAT IT CHANGED TO (value).
  This is required by NOM-024 and HIPAA's minimum-necessary standard.

ETL extraction example:
  SELECT
    id,
    occurred_at,
    event_type,
    payload->>'subject'           AS entity_id,
    payload->'data'->>'actor_role' AS role,
    payload->'data'->'after'->>'status' AS new_status
  FROM audit_logs
  WHERE clinic_id = ? AND occurred_at >= NOW() - INTERVAL '30 days';

GIN index on payload enables:
  WHERE payload @> '{"type": "appointment.status.changed"}'::jsonb

Analytics endpoint (monthly report):
  The analytics_service.py will query this table for:
    - Appointments attended vs canceled
    - Unique patients seen per doctor
    - Average appointment duration
    - Peak booking hours
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.clinic import Clinic
    from app.models.user import User


# ── Event type registry ───────────────────────────────────────────────────────
# Centralised here so autocomplete and typo-checking are easy.
# Format: <entity>.<action>.<detail>

class EventType:
    # Patient events
    PATIENT_CREATED = "patient.created"
    PATIENT_UPDATED = "patient.updated"
    PATIENT_DELETED = "patient.deleted"
    PATIENT_VIEWED = "patient.viewed"

    # Appointment events
    APPT_CREATED = "appointment.created"
    APPT_CONFIRMED = "appointment.confirmed"
    APPT_STATUS_CHANGED = "appointment.status.changed"
    APPT_CANCELED_STAFF = "appointment.canceled.staff"
    APPT_CANCELED_PATIENT = "appointment.canceled.patient"
    APPT_COMPLETED = "appointment.completed"
    APPT_NO_SHOW = "appointment.no_show"

    # Medical record events
    RECORD_CREATED = "medical_record.created"
    RECORD_VIEWED = "medical_record.viewed"
    RECORD_UPDATED = "medical_record.updated"
    RECORD_SIGNED = "medical_record.signed"
    RECORD_PDF_UPLOADED = "medical_record.pdf.uploaded"
    RECORD_PDF_DOWNLOADED = "medical_record.pdf.downloaded"

    # User / auth events
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login.failed"
    USER_CREATED = "user.created"
    USER_ROLE_CHANGED = "user.role.changed"
    USER_DEACTIVATED = "user.deactivated"

    # Billing events
    SUBSCRIPTION_STARTED = "billing.subscription.started"
    SUBSCRIPTION_RENEWED = "billing.subscription.renewed"
    SUBSCRIPTION_CANCELED = "billing.subscription.canceled"
    PAYMENT_FAILED = "billing.payment.failed"


# ── Model ─────────────────────────────────────────────────────────────────────

class AuditLog(TenantBase):
    """
    Append-only audit event. Inherits clinic_id, id, created_at, updated_at.

    The `occurred_at` column is separate from `created_at` because:
      • `created_at` = when the row was inserted (DB time, may lag under load)
      • `occurred_at` = when the event actually happened (application time, precise)
    For synchronous events these are nearly identical; they diverge for batch
    re-processing or event replay.

    The `updated_at` column inherited from TimestampMixin is present but
    should never change — the append-only contract is enforced by the service
    layer. A DB trigger can enforce this in production:
      CREATE OR REPLACE FUNCTION prevent_audit_update() ...
    """

    __tablename__ = "audit_logs"

    # ── Who ───────────────────────────────────────────────────────────────────
    actor_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        comment="The user who triggered this event. NULL = system/unauthenticated.",
    )

    actor_role: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        comment=(
            "Denormalised role snapshot at event time. "
            "Preserved even if the user's role changes later."
        ),
    )

    # ── What ──────────────────────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        index=True,
        comment="Dot-notation event type. See EventType class for registry.",
    )

    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Affected entity class name: Patient, Appointment, MedicalRecord, …",
    )

    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
        comment="PK of the affected row. Indexed for entity-history lookups.",
    )

    # ── When ──────────────────────────────────────────────────────────────────
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        comment="Application-layer timestamp of the event (not DB insert time).",
    )

    # ── Payload (CloudEvents JSONB) ───────────────────────────────────────────
    payload: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "CloudEvents-compatible envelope. "
            "PII values must be masked — only field names listed in masked_fields."
        ),
    )

    # ── Request context (stored flat for fast filtering) ─────────────────────
    ip_address: Mapped[Optional[str]] = mapped_column(
        String(45),       # IPv6 max length
        nullable=True,
    )

    user_agent: Mapped[Optional[str]] = mapped_column(
        String(300),
        nullable=True,
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    clinic: Mapped["Clinic"] = relationship(
        "Clinic",
        back_populates="audit_logs",
        lazy="select",
    )

    actor: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[actor_id],
        back_populates="audit_events",
        lazy="select",
    )

    # ── Composite indexes ─────────────────────────────────────────────────────
    __table_args__ = (
        # Time-range queries (most common: "events in the last 30 days")
        Index(
            "ix_audit_clinic_occurred",
            "clinic_id",
            "occurred_at",
        ),
        # Entity-history lookup: all events for a specific appointment/patient
        Index(
            "ix_audit_clinic_entity",
            "clinic_id",
            "entity_type",
            "entity_id",
            "occurred_at",
        ),
        # Filter by event type (e.g. all login failures)
        Index(
            "ix_audit_clinic_eventtype",
            "clinic_id",
            "event_type",
            "occurred_at",
        ),
        # GIN index on JSONB payload for arbitrary key-path queries and ETL
        # Created in Alembic as: op.create_index(..., postgresql_using='gin')
        # Listed here as a comment so the migration generator doesn't miss it.
        # Index("ix_audit_payload_gin", "payload", postgresql_using="gin"),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} type={self.event_type!r} "
            f"entity={self.entity_type}/{self.entity_id}>"
        )
