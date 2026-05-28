from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import AnyStaff, CurrentUser, DBSession, TenantContext, require_role, Role
from app.models.appointment import Appointment
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
        select(Appointment).where(Appointment.magic_token == token)
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
