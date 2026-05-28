from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select

from app.api.deps import OwnerOnly, TenantContext
from app.core.config import settings
from app.core.crypto import decrypt
from app.core.limiter import limiter
from app.models.clinic import Clinic
from app.models.user import User

router = APIRouter(prefix="/clinics", tags=["Clinics"])


# ── Shared helper ─────────────────────────────────────────────────────────────

async def resolve_notify_email(clinic: Clinic, db) -> str:
    """Return the effective notification email for a clinic.

    Priority: clinic.notify_email → owner's email → global CLINIC_NOTIFY_EMAIL.
    """
    if clinic.notify_email:
        return clinic.notify_email
    result = await db.execute(
        select(User).where(
            User.clinic_id == clinic.id,
            User.role == "owner",
            User.is_active.is_(True),
        ).limit(1)
    )
    owner = result.scalar_one_or_none()
    if owner and owner.email_enc:
        try:
            return decrypt(owner.email_enc)
        except Exception:
            pass
    return settings.CLINIC_NOTIFY_EMAIL


# ── Schemas ───────────────────────────────────────────────────────────────────

class NotifyEmailResponse(BaseModel):
    notify_email: Optional[str] = None


class NotifyEmailPatch(BaseModel):
    notify_email: Optional[EmailStr] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/me/notify-email", response_model=NotifyEmailResponse)
@limiter.limit("30/minute")
async def get_notify_email(
    request: Request,
    ctx: TenantContext,
    _: OwnerOnly,
) -> NotifyEmailResponse:
    result = await ctx.db.execute(
        select(Clinic.notify_email).where(Clinic.id == ctx.clinic_id)
    )
    notify_email = result.scalar_one_or_none()
    return NotifyEmailResponse(notify_email=notify_email)


@router.patch("/notify-email", response_model=NotifyEmailResponse)
@limiter.limit("10/minute")
async def update_notify_email(
    request: Request,
    body: NotifyEmailPatch,
    ctx: TenantContext,
    _: OwnerOnly,
) -> NotifyEmailResponse:
    result = await ctx.db.execute(
        select(Clinic).where(Clinic.id == ctx.clinic_id)
    )
    clinic = result.scalar_one_or_none()
    if clinic is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinic not found.")
    clinic.notify_email = str(body.notify_email) if body.notify_email else None
    await ctx.db.commit()
    return NotifyEmailResponse(notify_email=clinic.notify_email)
