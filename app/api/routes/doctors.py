from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.deps import AnyStaff, CurrentUser, DoctorOrAbove, OwnerOnly, Role, TenantContext
from app.api.routes.appointments import _appt_to_ics_event, _build_ics, _dt_to_ics
from app.core.crypto import decrypt
from app.core.limiter import limiter
from app.models.appointment import ACTIVE_STATUSES, Appointment
from app.models.doctor import Doctor
from app.models.user import User
from app.schemas.appointment import SlotItem, SlotsResponse
from app.schemas.doctor import (
    DoctorAvailableResponse,
    DoctorCreate,
    DoctorResponse,
    DoctorUpdate,
    WorkingHoursUpdateRequest,
)

_DAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_MAX_PHOTO_BYTES = 5 * 1024 * 1024  # 5 MB

router = APIRouter(prefix="/doctors", tags=["Doctors"])


def _build_response(doctor: Doctor) -> DoctorResponse:
    data = DoctorResponse.model_validate(doctor).model_dump()
    if doctor.user:
        data["full_name"] = decrypt(doctor.user.full_name_enc)
        if doctor.user.email_enc:
            try:
                data["email"] = decrypt(doctor.user.email_enc)
            except Exception:
                pass
        if doctor.user.phone_enc:
            try:
                data["phone"] = decrypt(doctor.user.phone_enc)
            except Exception:
                pass
    return DoctorResponse(**data)


async def _get_doctor_or_404(db, clinic_id: uuid.UUID, doctor_id: uuid.UUID) -> Doctor:
    result = await db.execute(
        select(Doctor).options(selectinload(Doctor.user)).where(
            Doctor.clinic_id == clinic_id,
            Doctor.id == doctor_id,
        )
    )
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")
    return doctor


@router.post("", response_model=DoctorResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
async def create_doctor(
    request: Request,
    body: DoctorCreate,
    ctx: TenantContext,
    _: OwnerOnly,
):
    result = await ctx.db.execute(
        select(User).where(
            User.id == body.user_id,
            User.clinic_id == ctx.clinic_id,
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in this clinic.",
        )

    existing = await ctx.db.execute(
        select(Doctor).where(Doctor.user_id == body.user_id)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A doctor profile already exists for this user.",
        )

    doctor = Doctor(
        clinic_id=ctx.clinic_id,
        user_id=body.user_id,
        specialty=body.specialty,
        bio=body.bio,
        avatar_s3_key=body.avatar_s3_key,
        working_hours=body.working_hours or {},
        appointment_duration_minutes=body.appointment_duration_minutes,
        is_accepting_patients=body.is_accepting_patients,
    )
    ctx.db.add(doctor)
    await ctx.db.commit()
    result = await ctx.db.execute(
        select(Doctor).options(selectinload(Doctor.user)).where(
            Doctor.id == doctor.id)
    )
    doctor = result.scalar_one()
    return _build_response(doctor)


@router.get("", response_model=List[DoctorResponse])
@limiter.limit("100/minute")
async def list_doctors(
    request: Request,
    ctx: TenantContext,
    _: AnyStaff,
    accepting_only: bool = False,
):
    query = select(Doctor).options(selectinload(Doctor.user)
                                   ).where(Doctor.clinic_id == ctx.clinic_id)
    if accepting_only:
        query = query.where(Doctor.is_accepting_patients.is_(True))
    result = await ctx.db.execute(query)
    return [_build_response(d) for d in result.scalars().all()]


@router.get("/available", response_model=List[DoctorAvailableResponse])
@limiter.limit("100/minute")
async def get_available_doctors(
    request: Request,
    ctx: TenantContext,
    _: AnyStaff,
    date: date = Query(..., description="Date to query (YYYY-MM-DD)"),
):
    """Return doctors who work on the given date (per working_hours) and are accepting patients."""
    day_abbr = _DAY_ABBR[date.weekday()]

    result = await ctx.db.execute(
        select(Doctor)
        .options(selectinload(Doctor.user))
        .where(
            Doctor.clinic_id == ctx.clinic_id,
            Doctor.is_accepting_patients.is_(True),
        )
    )
    doctors = result.scalars().all()

    available: List[DoctorAvailableResponse] = []
    for doctor in doctors:
        day_slots = (doctor.working_hours or {}).get(day_abbr, [])
        if not day_slots:
            continue
        available.append(
            DoctorAvailableResponse(
                id=doctor.id,
                clinic_id=doctor.clinic_id,
                user_id=doctor.user_id,
                full_name=decrypt(doctor.user.full_name_enc) if doctor.user else None,
                specialty=doctor.specialty,
                bio=doctor.bio,
                avatar_s3_key=doctor.avatar_s3_key,
                appointment_duration_minutes=doctor.appointment_duration_minutes,
                is_accepting_patients=doctor.is_accepting_patients,
                day_slots=day_slots,
                created_at=doctor.created_at,
                updated_at=doctor.updated_at,
            )
        )

    return available


@router.get("/{doctor_id}/available-slots", response_model=SlotsResponse)
@limiter.limit("100/minute")
async def get_available_slots(
    request: Request,
    doctor_id: uuid.UUID,
    ctx: TenantContext,
    _: AnyStaff,
    slot_date: date = Query(..., alias="date",
                            description="Date to query (YYYY-MM-DD)"),
):
    result = await ctx.db.execute(
        select(Doctor).where(
            Doctor.clinic_id == ctx.clinic_id,
            Doctor.id == doctor_id,
        )
    )
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    day_start = datetime(slot_date.year, slot_date.month,
                         slot_date.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    booked_result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < day_end,
            Appointment.ends_at > day_start,
        )
    )
    booked = [(a.starts_at, a.ends_at) for a in booked_result.scalars().all()]

    duration = timedelta(minutes=max(doctor.appointment_duration_minutes, 1))
    day_schedule = (doctor.working_hours or {}).get(
        _DAY_ABBR[slot_date.weekday()], [])

    slots: list[SlotItem] = []
    for block in day_schedule:
        try:
            bsh, bsm = map(int, block["start"].split(":"))
            beh, bem = map(int, block["end"].split(":"))
        except (KeyError, ValueError):
            continue
        slot_start = datetime(slot_date.year, slot_date.month,
                              slot_date.day, bsh, bsm, tzinfo=timezone.utc)
        block_end = datetime(slot_date.year, slot_date.month,
                             slot_date.day, beh, bem, tzinfo=timezone.utc)
        while slot_start + duration <= block_end:
            slot_end = slot_start + duration
            if not any(bs < slot_end and be > slot_start for bs, be in booked):
                slots.append(SlotItem(starts_at=slot_start, ends_at=slot_end))
            slot_start = slot_end

    return SlotsResponse(doctor_id=doctor_id, slots=slots)


@router.get("/{doctor_id}", response_model=DoctorResponse)
@limiter.limit("100/minute")
async def get_doctor(
    request: Request,
    doctor_id: uuid.UUID,
    ctx: TenantContext,
    _: AnyStaff,
):
    doctor = await _get_doctor_or_404(ctx.db, ctx.clinic_id, doctor_id)
    return _build_response(doctor)


@router.put("/{doctor_id}/working-hours", response_model=DoctorResponse)
@limiter.limit("100/minute")
async def update_working_hours(
    request: Request,
    doctor_id: uuid.UUID,
    body: WorkingHoursUpdateRequest,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    """Owner can update any doctor; a doctor can only update their own schedule."""
    doctor = await _get_doctor_or_404(ctx.db, ctx.clinic_id, doctor_id)

    if current_user.role == Role.DOCTOR and doctor.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Doctors can only update their own working hours.",
        )

    if body.working_hours is not None:
        doctor.working_hours = body.working_hours
    if body.is_available is not None:
        doctor.is_accepting_patients = body.is_available

    await ctx.db.commit()
    result = await ctx.db.execute(
        select(Doctor).options(selectinload(Doctor.user)).where(
            Doctor.id == doctor.id)
    )
    doctor = result.scalar_one()
    return _build_response(doctor)


@router.put("/{doctor_id}", response_model=DoctorResponse)
@limiter.limit("100/minute")
async def update_doctor(
    request: Request,
    doctor_id: uuid.UUID,
    body: DoctorUpdate,
    ctx: TenantContext,
    _: OwnerOnly,
):
    doctor = await _get_doctor_or_404(ctx.db, ctx.clinic_id, doctor_id)

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(doctor, field, value)

    await ctx.db.commit()
    await ctx.db.refresh(doctor)
    result = await ctx.db.execute(
        select(Doctor).options(selectinload(Doctor.user)).where(
            Doctor.id == doctor.id)
    )
    doctor = result.scalar_one()
    return _build_response(doctor)


@router.post("/{doctor_id}/photo")
@limiter.limit("20/minute")
async def upload_doctor_photo(
    request: Request,
    doctor_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    photo: UploadFile = File(..., description="Profile photo (JPEG or PNG)"),
):
    """Owner can upload any doctor's photo; a doctor can only upload their own."""
    doctor = await _get_doctor_or_404(ctx.db, ctx.clinic_id, doctor_id)

    if current_user.role == Role.DOCTOR and doctor.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Doctors can only update their own photo.",
        )

    content_type = (photo.content_type or "").lower()
    if "jpeg" in content_type or "jpg" in content_type:
        ext = ".jpg"
    elif "png" in content_type:
        ext = ".png"
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only JPEG or PNG images are accepted.",
        )

    content = await photo.read()
    if len(content) > _MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Photo exceeds the 5 MB limit.",
        )

    upload_dir = Path("uploads") / "doctors"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{doctor_id}{ext}"
    dest.write_bytes(content)

    doctor.avatar_s3_key = dest.as_posix()
    await ctx.db.commit()

    return {"url": f"/{dest.as_posix()}", "filename": dest.name}


@router.get("/{doctor_id}/calendar.ics")
@limiter.limit("10/minute")
async def get_doctor_calendar_ics(
    request: Request,
    doctor_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
):
    """Export a single doctor's appointments as iCalendar. Doctors can only access their own."""
    doctor = await _get_doctor_or_404(ctx.db, ctx.clinic_id, doctor_id)

    if Role(current_user.role) == Role.DOCTOR and doctor.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puedes ver tu propio calendario.",
        )

    now = datetime.now(timezone.utc)
    q = (
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.location),
        )
        .where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.doctor_id == doctor_id,
        )
    )

    q_start = (
        datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        if start_date else now - timedelta(days=7)
    )
    q = q.where(Appointment.starts_at >= q_start)
    if end_date is not None:
        q_end = (
            datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc)
            + timedelta(days=1)
        )
        q = q.where(Appointment.starts_at < q_end)

    q = q.order_by(Appointment.starts_at)
    result = await ctx.db.execute(q)
    appointments = result.scalars().unique().all()

    dtstamp = _dt_to_ics(now)
    events = [_appt_to_ics_event(appt, dtstamp) for appt in appointments]
    today = now.strftime("%Y%m%d")
    return Response(
        content=_build_ics(events),
        media_type="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="doctor_{doctor_id}_{today}.ics"',
        },
    )


@router.delete("/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("100/minute")
async def delete_doctor(
    request: Request,
    doctor_id: uuid.UUID,
    ctx: TenantContext,
    _: OwnerOnly,
):
    doctor = await _get_doctor_or_404(ctx.db, ctx.clinic_id, doctor_id)
    await ctx.db.delete(doctor)
    await ctx.db.commit()
