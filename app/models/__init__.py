"""
app/models/__init__.py
──────────────────────────────────────────────────────────────────────────────
Single import point for all ORM models.

Why this file matters:
  SQLAlchemy's mapper registry (and Alembic's autogenerate) only detects
  models that have been imported into the Python process. If you add a new
  model and forget to list it here, `alembic revision --autogenerate` will
  silently miss it and your migration will be incomplete.

  Rule: every new file in app/models/ MUST have a corresponding import here.

Import order follows FK dependency graph (parent before child) so that
  1. __tablename__ strings are registered before ForeignKey() references
     are resolved by SQLAlchemy's mapper configuration step.
  2. `relationship()` string references ("Clinic", "User", …) resolve
     correctly because all classes exist in the mapper registry.

Circular import prevention:
  All inter-model references inside model files use:
    from __future__ import annotations   (deferred evaluation of type hints)
    TYPE_CHECKING guard for relationship type hints
  This means the actual class objects are never imported inside model files
  at module load time — only here in __init__.py are all classes loaded
  together in a safe order.
"""

# ── 1. Root base (must be first — all models reference Base.metadata) ─────────
from app.models.base import Base, SystemBase, TenantBase, TimestampMixin  # noqa: F401

# ── 2. Tenant root (no FK dependencies) ──────────────────────────────────────
from app.models.clinic import Clinic, PlanTier, SubscriptionStatus  # noqa: F401

# ── 3. First-level tenant children (FK → clinics only) ────────────────────────
from app.models.user import User, Role  # noqa: F401

# ── 4. Doctor (FK → users + clinics) ─────────────────────────────────────────
from app.models.doctor import Doctor  # noqa: F401

# ── 5. Patient (FK → clinics only) ───────────────────────────────────────────
from app.models.patient import Patient  # noqa: F401

# ── 5b. Location (FK → clinics only) ─────────────────────────────────────────
from app.models.location import Location  # noqa: F401

# ── 6. Appointment (FK → doctors, patients, users, clinics, locations) ────────
from app.models.appointment import (  # noqa: F401
    Appointment,
    AppointmentStatus,
    ACTIVE_STATUSES,
    CANCELED_STATUSES,
)

# ── 7. Medical record (FK → appointments, patients, doctors) ──────────────────
from app.models.medical_record import MedicalRecord  # noqa: F401

# ── 8. Audit log (FK → users, clinics — append-only) ─────────────────────────
from app.models.audit_log import AuditLog, EventType  # noqa: F401

# ── 9. Notification (FK → appointments) ──────────────────────────────────────
from app.models.notification import (  # noqa: F401
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationType,
)

# ── 10. RefreshToken (FK → users) ─────────────────────────────────────────────
from app.models.refresh_token import RefreshToken  # noqa: F401

# ── 10b. PasswordResetToken (FK → users) ──────────────────────────────────────
from app.models.password_reset_token import PasswordResetToken  # noqa: F401

# ── 11. RescheduleRequest (FK → appointments, users) ──────────────────────────
from app.models.reschedule_request import (  # noqa: F401
    RescheduleRequest,
    RescheduleRequestStatus,
)

# ── 12. AppointmentNote (FK → appointments, users, clinics) ───────────────────
from app.models.appointment_note import AppointmentNote  # noqa: F401

# ── 13. EmailVerification (system-level, no tenant FK) ────────────────────────
from app.models.email_verification import EmailVerification  # noqa: F401

# ── Public surface ─────────────────────────────────────────────────────────────
__all__ = [
    # Bases
    "Base", "SystemBase", "TenantBase", "TimestampMixin",
    # Models
    "Clinic", "User", "Doctor", "Patient", "Location",
    "Appointment", "AppointmentNote", "MedicalRecord", "AuditLog", "Notification",
    "RefreshToken", "PasswordResetToken", "RescheduleRequest", "EmailVerification",
    # Enums / constants
    "PlanTier", "SubscriptionStatus", "Role",
    "AppointmentStatus", "ACTIVE_STATUSES", "CANCELED_STATUSES",
    "EventType",
    "NotificationChannel", "NotificationStatus", "NotificationType",
    "RescheduleRequestStatus",
]
