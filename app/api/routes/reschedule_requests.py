from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AnyStaff, CurrentUser, DBSession, TenantContext, require_role, Role
from app.api.routes.clinics import resolve_notify_email
from app.core.crypto import decrypt
from app.core.email import send_reschedule_request_notification
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.reschedule_request import RescheduleRequest, RescheduleRequestStatus
from app.schemas.reschedule_request import (
    RescheduleRequestCreate,
    RescheduleRequestResolve,
    RescheduleRequestResponse,
)

router = APIRouter(prefix="/reschedule-requests", tags=["Reschedule Requests"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _load_public_appt(token: str, db) -> Appointment:
    result = await db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.clinic),
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
        .where(Appointment.magic_token == token)
    )
    appt = result.scalar_one_or_none()
    if appt is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired link.",
        )
    if appt.magic_token_expires_at < datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This link has expired. Please request a new one.",
        )
    return appt


def _safe_decrypt(enc: str | None, fallback: str) -> str:
    if not enc:
        return fallback
    try:
        return decrypt(enc)
    except Exception:
        return fallback


# ── Public: patient submits request via magic token ───────────────────────────

@router.post(
    "/public/{token}",
    response_model=RescheduleRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Patient requests reschedule (public, magic token)",
)
async def create_reschedule_request(
    token: str,
    body: RescheduleRequestCreate,
    db: DBSession,
    background_tasks: BackgroundTasks,
) -> RescheduleRequestResponse:
    appt = await _load_public_appt(token, db)

    req = RescheduleRequest(
        clinic_id=appt.clinic_id,
        appointment_id=appt.id,
        patient_note=body.patient_note,
        status=RescheduleRequestStatus.PENDING,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    patient_name = _safe_decrypt(appt.patient.full_name_enc if appt.patient else None, "Paciente")
    doctor_name = "Doctor"
    if appt.doctor and appt.doctor.user:
        doctor_name = _safe_decrypt(appt.doctor.user.full_name_enc, "Doctor")

    notify_email = await resolve_notify_email(appt.clinic, db)
    send_reschedule_request_notification(
        notify_email=notify_email,
        patient_name=patient_name,
        doctor_name=doctor_name,
        starts_at=appt.starts_at,
        patient_note=body.patient_note,
        background_tasks=background_tasks,
    )

    return RescheduleRequestResponse.model_validate(req)


# ── Staff: list requests ──────────────────────────────────────────────────────

@router.get(
    "",
    response_model=List[RescheduleRequestResponse],
    summary="List reschedule requests (staff only)",
)
async def list_reschedule_requests(
    ctx: TenantContext,
    _: AnyStaff,
    status_filter: Optional[str] = Query(None, alias="status"),
) -> List[RescheduleRequestResponse]:
    q = select(RescheduleRequest).where(
        RescheduleRequest.clinic_id == ctx.clinic_id
    )
    if status_filter is not None:
        try:
            RescheduleRequestStatus(status_filter)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid status '{status_filter}'. Valid: pending, resolved, ignored.",
            )
        q = q.where(RescheduleRequest.status == status_filter)

    q = q.order_by(RescheduleRequest.requested_at.desc())
    result = await ctx.db.execute(q)
    rows = result.scalars().all()
    return [RescheduleRequestResponse.model_validate(r) for r in rows]


# ── Staff: resolve / ignore a request ────────────────────────────────────────

@router.patch(
    "/{request_id}/resolve",
    response_model=RescheduleRequestResponse,
    summary="Resolve or ignore a reschedule request (staff only)",
)
async def resolve_request(
    request_id: uuid.UUID,
    body: RescheduleRequestResolve,
    staff: CurrentUser,
    ctx: TenantContext,
) -> RescheduleRequestResponse:
    result = await ctx.db.execute(
        select(RescheduleRequest).where(
            RescheduleRequest.clinic_id == ctx.clinic_id,
            RescheduleRequest.id == request_id,
        )
    )
    req = result.scalar_one_or_none()
    if req is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reschedule request not found.",
        )
    if req.status != RescheduleRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Request is already '{req.status}'.",
        )

    if body.status == RescheduleRequestStatus.PENDING:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Cannot set status back to 'pending'.",
        )

    req.status = body.status
    req.resolved_at = datetime.now(timezone.utc)
    req.resolved_by_id = staff.id

    await ctx.db.commit()
    await ctx.db.refresh(req)
    return RescheduleRequestResponse.model_validate(req)
