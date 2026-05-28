"""
app/api/deps.py
──────────────────────────────────────────────────────────────────────────────
FastAPI dependency functions for auth, RBAC, and multi-tenant isolation.

Dependency chain (innermost → outermost):
    get_db()
        └─ get_current_user(token, db)        — validates JWT, loads User row
              └─ get_clinic(user)             — extracts & validates clinic_id
                    └─ require_role(...)      — RBAC gate for specific endpoints

Usage in router:
    @router.get("/patients")
    async def list_patients(
        clinic: ClinicContext = Depends(get_clinic),
        db: AsyncSession     = Depends(get_db),
        _: User              = Depends(require_role(Role.DOCTOR, Role.OWNER)),
    ):
        ...

Security invariants:
  1. Every authenticated endpoint gets a `ClinicContext` — never a raw clinic_id
     string. This ensures the DB session and clinic_id travel together and
     forces the dev to use the context object in repo calls.
  2. `get_clinic` re-validates the clinic_id from the TOKEN against the DB
     (active subscription check). A deactivated clinic is rejected even with a
     valid JWT until the token expires.
  3. Role hierarchy is checked in `require_role` — OWNER passes all role gates,
     DOCTOR passes DOCTOR+ASSISTANT gates, ASSISTANT passes only ASSISTANT gate.
  4. The magic link endpoint uses `get_public_appointment` (no JWT required)
     with a signed time-limited token — it never goes through get_current_user.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db

# ── Role definitions ──────────────────────────────────────────────────────────

class Role(StrEnum):
    OWNER     = "owner"
    DOCTOR    = "doctor"
    ASSISTANT = "assistant"


# Role hierarchy: higher-indexed roles inherit all gates of lower-indexed roles.
# OWNER can do everything DOCTOR and ASSISTANT can, plus billing/admin actions.
_ROLE_HIERARCHY: dict[Role, int] = {
    Role.ASSISTANT: 0,
    Role.DOCTOR:    1,
    Role.OWNER:     2,
}


def role_gte(user_role: Role, required: Role) -> bool:
    """True if user_role is at least as privileged as required."""
    return _ROLE_HIERARCHY.get(user_role, -1) >= _ROLE_HIERARCHY[required]


# ── Bearer token extractor ────────────────────────────────────────────────────

_bearer_scheme = HTTPBearer(auto_error=False)


def _extract_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """
    Pull the raw JWT string from the Authorization: Bearer <token> header.
    Returns 401 if the header is absent or malformed.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials.credentials


# ── JWT payload dataclass ─────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class TokenPayload:
    """
    Typed representation of our JWT claims.

    JWT structure (custom claims in addition to standard iss/exp/iat):
        {
          "sub":        "<user_uuid>",
          "clinic_id":  "<clinic_uuid>",
          "role":       "owner|doctor|assistant",
          "exp":        <unix_timestamp>,
          "iat":        <unix_timestamp>
        }

    We embed clinic_id and role in the token so that:
      • Most requests need 0 extra DB queries for auth (sub-millisecond).
      • clinic_id is still re-validated against the DB (active check) in
        get_clinic(), so a revoked/suspended clinic is caught on next request
        after the DB check (worst case: token TTL = 30 min).
    """
    sub:       uuid.UUID
    clinic_id: uuid.UUID
    role:      Role


# ── JWT decode ────────────────────────────────────────────────────────────────

_CREDENTIALS_EXCEPTION = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _decode_token(raw_token: str) -> TokenPayload:
    """
    Decode and validate a JWT, returning a typed TokenPayload.

    Raises HTTP 401 on:
      • Expired token
      • Invalid signature
      • Missing required claims
      • Malformed UUID / role values
    """
    try:
        payload = jwt.decode(
            raw_token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise _CREDENTIALS_EXCEPTION

    # Validate and type-cast custom claims
    try:
        return TokenPayload(
            sub=uuid.UUID(payload["sub"]),
            clinic_id=uuid.UUID(payload["clinic_id"]),
            role=Role(payload["role"]),
        )
    except (KeyError, ValueError):
        # Missing claim, bad UUID format, or unknown role string
        raise _CREDENTIALS_EXCEPTION


# ── User model (imported lazily to avoid circular imports) ────────────────────

def _get_user_model():
    from app.models.user import User  # noqa: PLC0415
    return User


def _get_clinic_model():
    from app.models.clinic import Clinic  # noqa: PLC0415
    return Clinic


# ── get_current_user ──────────────────────────────────────────────────────────

async def get_current_user(
    token: Annotated[str, Depends(_extract_token)],
    db:    Annotated[AsyncSession, Depends(get_db)],
):
    """
    Decode the JWT and load the User row from the DB.

    Returns the ORM User instance so downstream deps can access any user field.
    Raises HTTP 401 if the token is invalid or the user no longer exists.
    Raises HTTP 403 if the user account is deactivated.
    """
    payload = _decode_token(token)

    User = _get_user_model()
    result = await db.execute(
        select(User).where(
            User.id        == payload.sub,
            User.clinic_id == payload.clinic_id,  # always scope to tenant
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise _CREDENTIALS_EXCEPTION

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )

    return user


# ── ClinicContext ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ClinicContext:
    """
    Injected into every authenticated endpoint.

    Bundles the validated clinic_id with the DB session so repository methods
    receive both in a single argument — reducing signature noise and making it
    impossible to accidentally use the wrong session for the clinic.

    Usage:
        async def list_patients(ctx: ClinicContext = Depends(get_clinic)):
            patients = await patient_repo.list(ctx.db, ctx.clinic_id)
    """
    clinic_id: uuid.UUID
    db:        AsyncSession


# ── get_clinic — the multi-tenant isolation gate ──────────────────────────────

async def get_clinic(
    user: Annotated[object, Depends(get_current_user)],
    db:   Annotated[AsyncSession, Depends(get_db)],
) -> ClinicContext:
    """
    Validate that the user's clinic is active and return a ClinicContext.

    This is the multi-tenant enforcement point:
      1. The clinic_id comes from the JWT (via get_current_user).
      2. We verify the clinic exists AND is not suspended/deleted.
      3. We return a ClinicContext that downstream code uses for ALL queries.

    A ClinicContext in a route signature is the contract that the endpoint is
    tenant-safe. Routes that accept raw `clinic_id: uuid.UUID` from the request
    body are a code-review red flag.

    Why re-check the DB on every request?
      The JWT may be valid but the clinic's subscription may have lapsed.
      This check costs ~1 ms (PK lookup, usually in Postgres buffer cache) and
      is the correct trade-off for a medical SaaS where data isolation is critical.
    """
    Clinic = _get_clinic_model()
    result = await db.execute(
        select(Clinic).where(Clinic.id == user.clinic_id)
    )
    clinic = result.scalar_one_or_none()

    if clinic is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clinic not found.",
        )

    if getattr(clinic, "is_suspended", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clinic account is suspended. Please contact support.",
        )

    return ClinicContext(clinic_id=clinic.id, db=db)


# ── RBAC: require_role factory ────────────────────────────────────────────────

def require_role(*roles: Role):
    """
    Factory that returns a FastAPI dependency enforcing RBAC.

    Respects the role hierarchy: OWNER satisfies any role gate.

    Usage:
        # Only OWNER and DOCTOR can create patients:
        @router.post("/patients")
        async def create_patient(
            _: Annotated[object, Depends(require_role(Role.DOCTOR, Role.OWNER))],
            ctx: ClinicContext = Depends(get_clinic),
        ):
            ...

        # ASSISTANT can confirm appointments:
        @router.patch("/appointments/{id}/confirm")
        async def confirm_appointment(
            _: Annotated[object, Depends(require_role(Role.ASSISTANT))],
            ...
        ):
            ...
    """
    min_required = min(roles, key=lambda r: _ROLE_HIERARCHY[r])

    async def _check_role(
        user: Annotated[object, Depends(get_current_user)],
    ):
        if not role_gte(Role(user.role), min_required):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires one of the following roles: "
                    f"{[r.value for r in roles]}. "
                    f"Your role is '{user.role}'."
                ),
            )
        return user

    return _check_role


# ── Magic link: public appointment token validation ───────────────────────────

async def get_public_appointment(
    token: str,
    db:    Annotated[AsyncSession, Depends(get_db)],
):
    """
    Validate a magic-link token for the patient-facing booking / cancel flow.

    This dependency is used by endpoints under /public/* that do NOT require a
    JWT — they accept only the short-lived magic token stored in the DB.

    Security properties:
      • Token is a random UUID generated at appointment creation time.
      • Expiry is stored in `magic_token_expires_at` and checked here.
      • Token is single-use: on successful cancel/postpone, it is cleared.
      • No clinic_id is needed — the appointment row carries it.
    """
    from datetime import datetime, timezone  # noqa: PLC0415
    from app.models.appointment import Appointment  # noqa: PLC0415

    result = await db.execute(
        select(Appointment).where(Appointment.magic_token == token)
    )
    appointment = result.scalar_one_or_none()

    if appointment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired link.",
        )

    if appointment.magic_token_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This link has expired. Please request a new one.",
        )

    return appointment


# ── Typed aliases for cleaner route signatures ────────────────────────────────

CurrentUser   = Annotated[object, Depends(get_current_user)]
TenantContext = Annotated[ClinicContext, Depends(get_clinic)]
OwnerOnly     = Annotated[object, Depends(require_role(Role.OWNER))]
DoctorOrAbove = Annotated[object, Depends(require_role(Role.DOCTOR))]
AnyStaff      = Annotated[object, Depends(require_role(Role.ASSISTANT))]
DBSession     = Annotated[AsyncSession, Depends(get_db)]
