"""
app/models/base.py
──────────────────────────────────────────────────────────────────────────────
SQLAlchemy declarative base classes for the multi-tenant schema.

Hierarchy:
    Base          ← plain DeclarativeBase, used by Alembic
    TimestampMixin← adds created_at / updated_at to any model
    TenantBase    ← Base + TimestampMixin + clinic_id (most models inherit this)
    SystemBase    ← Base + TimestampMixin only (for Clinics itself, no tenant FK)

Why two bases?
  • The `clinics` table IS the tenant root — it must not reference itself.
  • Every other table uses TenantBase which enforces the clinic_id FK and index.

Tenant isolation strategy — "Shared Schema":
  Every TenantBase subclass gets:
    1. `clinic_id UUID NOT NULL`           — FK → clinics.id
    2. `ix_{tablename}_clinic_id` index    — fast per-tenant scans
    3. The application layer ALWAYS filters WHERE clinic_id = :cid.
       This is enforced in deps.py via get_clinic() + query scoping in repos.
  No RLS is used at the DB level in the MVP (adds complexity with async drivers),
  but the clinic_id index + app-layer enforcement is equivalent for the threat
  model: a compromised JWT can only access its own clinic_id.

Updated_at auto-update:
  SQLAlchemy's `onupdate` fires on `session.flush()` — no DB trigger needed.
  This keeps the logic in Python and works with any Postgres version.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, event, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    declared_attr,
    mapped_column,
    Session,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    """Timezone-aware UTC now. Avoids the deprecated datetime.utcnow()."""
    return datetime.now(timezone.utc)


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


# ── Root declarative base ─────────────────────────────────────────────────────

class Base(DeclarativeBase):
    """
    Root declarative base.
    Only Alembic and the two derived bases (SystemBase, TenantBase) use this
    directly. Application models should inherit from TenantBase or SystemBase.
    """

    # Map Python uuid.UUID → PostgreSQL UUID natively (no CHAR(32) fallback)
    type_annotation_map = {
        uuid.UUID: PG_UUID(as_uuid=True),
    }

    # ── Default __tablename__ ─────────────────────────────────────────────
    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """
        Auto-derive table name from class name (CamelCase → snake_case).
        Override in the model if you need a custom name.

        Examples:
            Patient          → patients
            MedicalRecord    → medical_records
            AuditLog         → audit_logs
        """
        import re
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()
        # Pluralise naively — override __tablename__ for irregular plurals
        return name + "s"

    def to_dict(self) -> dict[str, Any]:
        """Shallow dict of column values. Useful for audit log payloads."""
        return {
            col.key: getattr(self, col.key)
            for col in self.__table__.columns
        }


# ── Timestamp mixin ───────────────────────────────────────────────────────────

class TimestampMixin:
    """
    Adds `created_at` and `updated_at` to any model.

    Both are stored as TIMESTAMP WITH TIME ZONE (via timezone=True).
    `updated_at` is set to the current time on every flush that touches the row.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
        # server_default is a fallback for rows inserted via raw SQL (migrations)
        server_default=text("NOW()"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,          # SQLAlchemy sets this on every UPDATE flush
        nullable=False,
        server_default=text("NOW()"),
    )


# ── SystemBase — for tenant-root tables (Clinic, Plan, …) ────────────────────

class SystemBase(Base, TimestampMixin):
    """
    Use for system-level tables that ARE NOT scoped to a clinic.
    Currently: Clinic (the tenant root itself).

    Provides: id (UUID PK) + created_at + updated_at.
    Does NOT provide: clinic_id (would be a self-referential FK on Clinic).
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=_new_uuid,
        # gen_random_uuid() as server default ensures IDs even for raw SQL inserts
        server_default=text("gen_random_uuid()"),
    )


# ── TenantBase — for all multi-tenant tables ──────────────────────────────────

class TenantBase(Base, TimestampMixin):
    """
    Use for every table that belongs to a clinic (Patient, Appointment, …).

    Automatically provides:
        id          UUID PK
        clinic_id   UUID FK → clinics.id   (tenant discriminator)
        created_at  TIMESTAMPTZ
        updated_at  TIMESTAMPTZ

    Index strategy:
        • ix_{table}_clinic_id          — base tenant filter
        • Composite indexes (clinic_id, X) are defined in the concrete models
          where needed (e.g. appointments: clinic_id + starts_at).

    IMPORTANT — repositories must ALWAYS include the clinic_id filter:
        session.query(Patient).filter(
            Patient.clinic_id == clinic_id,
            Patient.id        == patient_id,
        )
    Omitting clinic_id is a cross-tenant data leak.
    """

    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=_new_uuid,
        server_default=text("gen_random_uuid()"),
    )

    # clinic_id is declared as a plain Column here (not as a relationship)
    # so that it's available on every model without requiring an explicit import
    # of the Clinic model (avoiding circular imports in model files).
    clinic_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("clinics.id", ondelete="CASCADE"),
        nullable=False,
        index=True,     # ix_{tablename}_clinic_id — auto-named by SA
    )

    # ── Additional composite indexes (declared per-table via __table_args__) ─
    # Example in a concrete model:
    #
    #   __table_args__ = (
    #       Index("ix_appointments_clinic_starts", "clinic_id", "starts_at"),
    #       Index("ix_appointments_clinic_status", "clinic_id", "status"),
    #   )
    #
    # The base index (ix_{table}_clinic_id) is sufficient for most queries;
    # composite indexes add value only for high-frequency filtered queries.

    @declared_attr.directive
    def __table_args__(cls) -> tuple:  # noqa: N805
        """
        Base table args. Concrete models can extend this:

            @declared_attr.directive
            def __table_args__(cls):
                base = super().__table_args__  # () from here
                return base + (Index(...),)
        """
        return ()


# ── SQLAlchemy event hook: guard against missing clinic_id on flush ───────────

@event.listens_for(Session, "before_flush")
def _enforce_clinic_id(session: Session, flush_context: Any, instances: Any) -> None:
    """
    Development-time guard: raise immediately if a TenantBase instance is about
    to be flushed without a clinic_id.

    This catches programming errors (e.g. forgot to set clinic_id in a service)
    before they reach the DB constraint, giving a cleaner error message.

    The DB FK constraint is the real safety net; this hook is a dev UX aid.
    """
    for obj in session.new:
        if isinstance(obj, TenantBase) and obj.clinic_id is None:
            raise ValueError(
                f"{obj.__class__.__name__} instance is missing `clinic_id`. "
                "Every tenant-scoped model must have clinic_id set before flush. "
                "Check the service or repository that created this instance."
            )
