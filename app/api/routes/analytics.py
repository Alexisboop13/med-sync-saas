from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, func, select

from app.api.deps import CurrentUser, OwnerOnly, Role, TenantContext
from app.core.crypto import decrypt
from app.models.appointment import Appointment, AppointmentStatus
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# ── Response schemas ──────────────────────────────────────────────────────────

class AppointmentMonthStat(BaseModel):
    year: int
    month: int
    scheduled: int
    completed: int
    canceled: int


class PatientMonthStat(BaseModel):
    year: int
    month: int
    count: int


class TopDoctor(BaseModel):
    doctor_id: uuid.UUID
    name: str
    appointments: int


class DashboardResponse(BaseModel):
    appointments_by_month: List[AppointmentMonthStat]
    new_patients_by_month: List[PatientMonthStat]
    top_doctors: List[TopDoctor]


class OwnerDashboardResponse(BaseModel):
    total_patients: int
    total_appointments_today: int
    total_appointments_this_month: int
    total_doctors: int
    appointments_by_status: dict[str, int]
    revenue_this_month: Optional[float] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _since_twelve_months() -> datetime:
    now = datetime.now(timezone.utc)
    return datetime(now.year - 1, now.month, 1, tzinfo=timezone.utc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/owner-dashboard", response_model=OwnerDashboardResponse)
async def get_owner_dashboard(
    ctx: TenantContext,
    _: OwnerOnly,
):
    db = ctx.db
    clinic_id = ctx.clinic_id
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    today_end = today_start + timedelta(days=1)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    total_patients, total_doctors = await _count_patients_and_doctors(db, clinic_id)

    appt_row = (await db.execute(
        select(
            func.count(
                case((
                    (Appointment.starts_at >= today_start) & (Appointment.starts_at < today_end),
                    1,
                ), else_=None)
            ).label("today"),
            func.count(
                case((Appointment.starts_at >= month_start, 1), else_=None)
            ).label("this_month"),
            func.count(
                case((Appointment.status.in_(["scheduled", "confirmed", "in_progress"]), 1), else_=None)
            ).label("scheduled"),
            func.count(
                case((Appointment.status == AppointmentStatus.COMPLETED, 1), else_=None)
            ).label("completed"),
            func.count(
                case((Appointment.status.in_(["canceled", "canceled_by_patient", "no_show"]), 1), else_=None)
            ).label("canceled"),
        )
        .where(Appointment.clinic_id == clinic_id)
    )).one()

    return OwnerDashboardResponse(
        total_patients=total_patients,
        total_appointments_today=appt_row.today,
        total_appointments_this_month=appt_row.this_month,
        total_doctors=total_doctors,
        appointments_by_status={
            "scheduled": appt_row.scheduled,
            "completed": appt_row.completed,
            "canceled": appt_row.canceled,
        },
    )


async def _count_patients_and_doctors(db, clinic_id) -> tuple[int, int]:
    patients, doctors = await asyncio.gather(
        db.execute(
            select(func.count(Patient.id)).where(
                Patient.clinic_id == clinic_id,
                Patient.is_active.is_(True),
            )
        ),
        db.execute(
            select(func.count(Doctor.id)).where(Doctor.clinic_id == clinic_id)
        ),
    )
    return patients.scalar_one(), doctors.scalar_one()


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    ctx: TenantContext,
    current_user: CurrentUser,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    filter_doctor_id: Optional[uuid.UUID] = Query(None, alias="doctor_id"),
):
    db = ctx.db
    clinic_id = ctx.clinic_id

    since: datetime = (
        datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        if start_date is not None
        else _since_twelve_months()
    )
    until: Optional[datetime] = (
        datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
        if end_date is not None
        else None
    )

    # Resolve doctor_id filter
    doctor_filter: Optional[uuid.UUID] = None
    if current_user.role == Role.DOCTOR:
        row = await db.execute(
            select(Doctor.id).where(
                Doctor.clinic_id == clinic_id,
                Doctor.user_id == current_user.id,
            )
        )
        doctor_filter = row.scalar_one_or_none()
        if doctor_filter is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Doctor profile not found for this user.",
            )
    elif filter_doctor_id is not None:
        doctor_filter = filter_doctor_id

    # ── 1. Appointments by month ──────────────────────────────────────────────
    trunc = func.date_trunc("month", Appointment.starts_at)
    appt_q = (
        select(
            func.extract("year", trunc).label("year"),
            func.extract("month", trunc).label("month"),
            func.count(
                case(
                    (Appointment.status.in_(["scheduled", "confirmed", "in_progress"]), 1),
                    else_=None,
                )
            ).label("scheduled"),
            func.count(
                case(
                    (Appointment.status == AppointmentStatus.COMPLETED, 1),
                    else_=None,
                )
            ).label("completed"),
            func.count(
                case(
                    (Appointment.status.in_(["canceled", "canceled_by_patient", "no_show"]), 1),
                    else_=None,
                )
            ).label("canceled"),
        )
        .where(
            Appointment.clinic_id == clinic_id,
            Appointment.starts_at >= since,
        )
        .group_by(trunc)
        .order_by(trunc)
    )
    if until is not None:
        appt_q = appt_q.where(Appointment.starts_at < until)
    if doctor_filter:
        appt_q = appt_q.where(Appointment.doctor_id == doctor_filter)

    appointments_by_month = [
        AppointmentMonthStat(
            year=int(row.year),
            month=int(row.month),
            scheduled=row.scheduled,
            completed=row.completed,
            canceled=row.canceled,
        )
        for row in (await db.execute(appt_q)).all()
    ]

    # ── 2. New patients by month ──────────────────────────────────────────────
    p_trunc = func.date_trunc("month", Patient.created_at)
    patient_q = (
        select(
            func.extract("year", p_trunc).label("year"),
            func.extract("month", p_trunc).label("month"),
            func.count(Patient.id).label("count"),
        )
        .where(
            Patient.clinic_id == clinic_id,
            Patient.created_at >= since,
            Patient.is_active == True,  # noqa: E712
        )
        .group_by(p_trunc)
        .order_by(p_trunc)
    )
    if until is not None:
        patient_q = patient_q.where(Patient.created_at < until)
    if doctor_filter:
        doctor_patients = select(Appointment.patient_id.distinct()).where(
            Appointment.clinic_id == clinic_id,
            Appointment.doctor_id == doctor_filter,
        )
        patient_q = patient_q.where(Patient.id.in_(doctor_patients))

    new_patients_by_month = [
        PatientMonthStat(year=int(row.year), month=int(row.month), count=row.count)
        for row in (await db.execute(patient_q)).all()
    ]

    # ── 3. Top doctors ────────────────────────────────────────────────────────
    appt_count = func.count(Appointment.id)
    top_q = (
        select(
            Doctor.id.label("doctor_id"),
            User.full_name_enc.label("name"),
            appt_count.label("appt_count"),
        )
        .join(Appointment, Appointment.doctor_id == Doctor.id)
        .join(User, User.id == Doctor.user_id)
        .where(
            Doctor.clinic_id == clinic_id,
            Appointment.clinic_id == clinic_id,
            Appointment.starts_at >= since,
        )
        .group_by(Doctor.id, User.id, User.full_name_enc)
        .order_by(appt_count.desc())
        .limit(10)
    )
    if until is not None:
        top_q = top_q.where(Appointment.starts_at < until)
    if doctor_filter:
        top_q = top_q.where(Doctor.id == doctor_filter)

    top_doctors = [
        TopDoctor(doctor_id=row.doctor_id, name=decrypt(row.name), appointments=row.appt_count)
        for row in (await db.execute(top_q)).all()
    ]

    return DashboardResponse(
        appointments_by_month=appointments_by_month,
        new_patients_by_month=new_patients_by_month,
        top_doctors=top_doctors,
    )
