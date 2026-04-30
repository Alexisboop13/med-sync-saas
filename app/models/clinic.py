"""
app/models/clinic.py
──────────────────────────────────────────────────────────────────────────────
Clinic — the multi-tenant root entity.

Every other table in the system has a `clinic_id FK → clinics.id`.
This model intentionally inherits from SystemBase (NOT TenantBase) because:
  • It IS the tenant — it cannot reference itself.
  • It has no `clinic_id` column.

Subscription lifecycle (Stripe):
  TRIALING  →  ACTIVE  →  PAST_DUE  →  CANCELED
                 ↑              |
                 └──────────────┘  (payment recovered)

The `is_suspended` property is the single source of truth used by get_clinic()
in deps.py to block access when a subscription lapses.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import SystemBase

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.patient import Patient
    from app.models.appointment import Appointment
    from app.models.audit_log import AuditLog


# ── Subscription states ───────────────────────────────────────────────────────

class SubscriptionStatus(StrEnum):
    TRIALING = "trialing"    # free trial, full feature access
    ACTIVE = "active"      # paid and current
    PAST_DUE = "past_due"    # payment failed, grace period (72 h default)
    CANCELED = "canceled"    # deliberate cancellation
    SUSPENDED = "suspended"   # manual suspension by platform admin


# ── Plan tiers ────────────────────────────────────────────────────────────────

class PlanTier(StrEnum):
    STARTER = "starter"    # 1 doctor, 200 appointments/mo
    GROWTH = "growth"     # 5 doctors, unlimited appointments
    ENTERPRISE = "enterprise"  # unlimited, custom SLA, dedicated support


# ── Model ─────────────────────────────────────────────────────────────────────

class Clinic(SystemBase):
    """
    Tenant root. One row per subscribed clinic.

    Alembic note: because SystemBase has __abstract__ = True, Alembic will
    detect Clinic as the first concrete mapped table from this hierarchy.
    """

    __tablename__ = "clinics"

    # ── Identity ──────────────────────────────────────────────────────────────
    name: Mapped[str] = mapped_column(
        String(200),
        nullable=False,
        comment="Display name shown in the UI and on patient-facing pages.",
    )

    slug: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        unique=True,
        index=True,
        comment="URL-safe identifier used in magic links: /book/{slug}/...",
    )

    # ── Contact / settings ────────────────────────────────────────────────────
    country_code: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        default="MX",
        server_default="MX",
        comment="ISO 3166-1 alpha-2. Drives timezone defaults and tax rules.",
    )

    timezone: Mapped[str] = mapped_column(
        String(60),
        nullable=False,
        default="America/Mexico_City",
        server_default="America/Mexico_City",
    )

    logo_s3_key: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="S3 object key for the clinic logo (served via presigned URL).",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    # ── Subscription / Stripe ─────────────────────────────────────────────────
    plan_tier: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=PlanTier.STARTER,
        server_default="starter",
    )

    subscription_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=SubscriptionStatus.TRIALING,
        server_default="trialing",
        index=True,
        comment="Synced from Stripe webhooks. Drives access control in get_clinic().",
    )

    stripe_customer_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
        comment="cus_* Stripe customer ID. Set on first checkout.",
    )

    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        unique=True,
        index=True,
        comment="sub_* Stripe subscription ID. Null during trial.",
    )

    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When NULL, the clinic is on a paid plan.",
    )

    # ── Relationships (lazy='select' — avoid N+1 in list endpoints) ───────────
    users: Mapped[List["User"]] = relationship(
        "User",
        back_populates="clinic",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    patients: Mapped[List["Patient"]] = relationship(
        "Patient",
        back_populates="clinic",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment",
        back_populates="clinic",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    audit_logs: Mapped[List["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="clinic",
        lazy="select",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    # ── Additional indexes ────────────────────────────────────────────────────
    __table_args__ = (
        Index("ix_clinics_subscription_status", "subscription_status"),
        # already unique, explicit idx for clarity
        Index("ix_clinics_slug", "slug"),
    )

    # ── Business logic helpers ────────────────────────────────────────────────

    @property
    def is_suspended(self) -> bool:
        """
        True when the clinic should be denied access to the API.
        Used by get_clinic() in deps.py as the single access-gate check.

        PAST_DUE gets a grace window — Stripe retries for 72 h by default.
        During that window the clinic stays accessible; after 72 h Stripe moves
        it to CANCELED and we block on the next request.
        """
        return self.subscription_status in (
            SubscriptionStatus.CANCELED,
            SubscriptionStatus.SUSPENDED,
        )

    @property
    def on_trial(self) -> bool:
        return self.subscription_status == SubscriptionStatus.TRIALING

    def __repr__(self) -> str:
        return f"<Clinic id={self.id} slug={self.slug!r} plan={self.plan_tier}>"
