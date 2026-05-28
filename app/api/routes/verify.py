"""
app/api/routes/verify.py
──────────────────────────────────────────────────────────────────────────────
Email verification endpoints for the public booking flow. No JWT required.

Rate limiting strategy:
  • Per-IP via slowapi (10/min): blocks automated scanners.
  • Per-email via DB (3 sends/hour): prevents email flooding for a given address.

Endpoints:
  POST /api/v1/public/verify/send-code   → generates + emails a 6-digit code
  POST /api/v1/public/verify/check-code  → validates code, returns a short-lived token

The token returned by check-code is a one-time UUID4 stored in email_verifications.
The booking endpoint (POST /public/book/{slug}) accepts this token, verifies it
against the request email, then immediately invalidates it after the appointment
is created.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.crypto import make_search_hash
from app.core.email import send_verification_code_email
from app.core.limiter import limiter
from app.db.session import get_db
from app.models.email_verification import EmailVerification

router = APIRouter(tags=["Email Verification"])

DBSession = Annotated[object, Depends(get_db)]

_CODE_TTL_MINUTES = 15
_TOKEN_TTL_MINUTES = 30
_MAX_SENDS_PER_HOUR = 3


class SendCodeRequest(BaseModel):
    email: str = Field(..., max_length=200)


class CheckCodeRequest(BaseModel):
    email: str = Field(..., max_length=200)
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class CheckCodeResponse(BaseModel):
    verified: bool
    token: str


@router.post("/public/verify/send-code", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def send_code(
    request: Request,
    body: SendCodeRequest,
    background_tasks: BackgroundTasks,
    db: DBSession,
):
    """
    Send a 6-digit verification code to the given email.

    Rate limits:
      - 10 requests/min per IP (slowapi)
      - 3 sends/hour per email address (application logic in DB)
    """
    normalized = body.email.lower().strip()
    if "@" not in normalized or "." not in normalized.split("@")[-1]:
        raise HTTPException(status_code=422, detail="Email inválido.")

    email_hash = make_search_hash(normalized)
    now = datetime.now(timezone.utc)
    code = f"{random.randint(0, 999999):06d}"
    expires_at = now + timedelta(minutes=_CODE_TTL_MINUTES)

    result = await db.execute(
        select(EmailVerification).where(EmailVerification.email_hash == email_hash)
    )
    row = result.scalar_one_or_none()

    if row is not None:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        if now - created < timedelta(hours=1):
            if row.send_count >= _MAX_SENDS_PER_HOUR:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        "Has solicitado demasiados códigos para este correo. "
                        "Espera un momento antes de intentarlo de nuevo."
                    ),
                )
            row.send_count += 1
        else:
            # Hourly window expired — reset counter and anchor
            row.send_count = 1
            row.created_at = now

        row.code = code
        row.expires_at = expires_at
        # Invalidate any previous verification token when a new code is issued
        row.verification_token = None
        row.token_expires_at = None
        row.verified_at = None
    else:
        row = EmailVerification(
            email_hash=email_hash,
            code=code,
            expires_at=expires_at,
        )
        db.add(row)

    await db.commit()

    send_verification_code_email(
        to_email=normalized,
        code=code,
        background_tasks=background_tasks,
    )

    return {"message": "Código enviado. Revisa tu correo."}


@router.post("/public/verify/check-code", response_model=CheckCodeResponse)
@limiter.limit("10/minute")
async def check_code(
    request: Request,
    body: CheckCodeRequest,
    db: DBSession,
):
    """
    Validate a verification code. Returns a one-time token valid for 30 minutes.
    The token must be included in the subsequent booking request.
    """
    normalized = body.email.lower().strip()
    email_hash = make_search_hash(normalized)
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(EmailVerification).where(EmailVerification.email_hash == email_hash)
    )
    row = result.scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=400, detail="Código inválido o expirado.")

    expires = row.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        raise HTTPException(
            status_code=400,
            detail="El código ha expirado. Haz clic en 'Reenviar código' para obtener uno nuevo.",
        )

    if row.code != body.code:
        raise HTTPException(status_code=400, detail="Código incorrecto.")

    token = str(uuid.uuid4())
    row.verified_at = now
    row.verification_token = token
    row.token_expires_at = now + timedelta(minutes=_TOKEN_TTL_MINUTES)
    await db.commit()

    return CheckCodeResponse(verified=True, token=token)
