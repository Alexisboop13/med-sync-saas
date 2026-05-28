from __future__ import annotations

import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, outerjoin, select
from sqlalchemy.orm import selectinload

from app.api.deps import AnyStaff, ClinicContext, CurrentUser, DBSession, DoctorOrAbove, TenantContext
from app.core.crypto import DecryptionError, _load_keyring, decrypt
from app.core.config import settings
from app.core.email import send_appointment_confirmation, send_reschedule_proposal, send_reschedule_request_notification
from app.core.limiter import get_ip_and_clinic, limiter
from app.models.appointment import ACTIVE_STATUSES, Appointment, AppointmentStatus
from app.models.clinic import Clinic
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.models.user import Role
from app.models.appointment_note import AppointmentNote
from app.schemas.appointment import (
    AgendaResponse,
    AppointmentCreate,
    AppointmentNoteAdd,
    AppointmentNoteCreate,
    AppointmentNoteResponse,
    AppointmentNoteSet,
    AppointmentResponse,
    AppointmentStatusPatch,
    AppointmentUpdate,
    AppointmentWithRescheduleResponse,
    PaginatedAppointmentResponse,
    ProposeRescheduleBody,
    PublicAppointmentAction,
    PublicAppointmentResponse,
    SlotItem,
    SlotsResponse,
)
from app.schemas.medical_record import MedicalRecordResponse
from app.schemas.reschedule_request import RescheduleRequestPublicBody
from app.models.reschedule_request import RescheduleRequest, RescheduleRequestStatus

_MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB


def _key_version() -> int:
    return max(_load_keyring())


router = APIRouter(prefix="/appointments", tags=["Appointments"])


async def _assert_no_overlap(
    ctx: ClinicContext,
    doctor_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: Optional[uuid.UUID] = None,
) -> None:
    q = select(Appointment).where(
        Appointment.clinic_id == ctx.clinic_id,
        Appointment.doctor_id == doctor_id,
        Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
        Appointment.starts_at < ends_at,
        Appointment.ends_at > starts_at,
    )
    if exclude_id is not None:
        q = q.where(Appointment.id != exclude_id)
    result = await ctx.db.execute(q)
    conflict = result.scalar_one_or_none()
    if conflict is not None:
        tz_row = await ctx.db.execute(
            select(Clinic.timezone).where(Clinic.id == ctx.clinic_id)
        )
        tz_name = tz_row.scalar_one_or_none() or "America/Mexico_City"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("America/Mexico_City")

        s = conflict.starts_at.astimezone(tz)
        e = conflict.ends_at.astimezone(tz)

        def _fmt_12h(dt: datetime) -> str:
            h = dt.hour % 12 or 12
            period = "AM" if dt.hour < 12 else "PM"
            return f"{h}:{dt.strftime('%M')} {period}"

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El doctor ya tiene una cita el {s.strftime('%d/%m/%Y')} "
                f"de {_fmt_12h(s)} a {_fmt_12h(e)}. "
                f"Por favor, elige otro horario."
            ),
        )


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
async def create_appointment(
    request: Request,
    body: AppointmentCreate,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    background_tasks: BackgroundTasks,
):
    patient_result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == body.patient_id,
            Patient.is_active.is_(True),
        )
    )
    patient = patient_result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    doctor_result = await ctx.db.execute(
        select(Doctor)
        .options(selectinload(Doctor.user))
        .where(
            Doctor.clinic_id == ctx.clinic_id,
            Doctor.id == body.doctor_id,
        )
    )
    doctor = doctor_result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    await _assert_no_overlap(ctx, body.doctor_id, body.starts_at, body.ends_at)

    appointment = Appointment(
        clinic_id=ctx.clinic_id,
        doctor_id=body.doctor_id,
        patient_id=body.patient_id,
        location_id=body.location_id,
        created_by_id=current_user.id,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=AppointmentStatus.SCHEDULED,
        reason=body.reason,
        notes_enc=body.notes,
        magic_token=str(uuid.uuid4()),
        magic_token_expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    ctx.db.add(appointment)
    await ctx.db.commit()
    await ctx.db.refresh(appointment)

    send_appointment_confirmation(
        patient_email=patient.email_enc or "",
        patient_name=_decrypt_name(patient.full_name_enc, "Paciente"),
        doctor_name=_decrypt_name(
            doctor.user.full_name_enc if doctor.user else None, "Doctor"
        ),
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        reason=body.reason,
        background_tasks=background_tasks,
    )

    return appointment


@router.get("", response_model=PaginatedAppointmentResponse)
@limiter.limit("100/minute")
async def list_appointments(
    request: Request,
    ctx: TenantContext,
    _: AnyStaff,
    patient_id: Optional[uuid.UUID] = Query(None),
    doctor_id: Optional[uuid.UUID] = Query(None),
    fecha: Optional[date] = Query(None, description="Single day filter (YYYY-MM-DD)"),
    start_date: Optional[date] = Query(None, description="Range start (YYYY-MM-DD), ignored when fecha is set"),
    end_date: Optional[date] = Query(None, description="Range end inclusive (YYYY-MM-DD), ignored when fecha is set"),
    appt_status: Optional[str] = Query(None, alias="status", description="Filter by appointment status"),
    patient_q: Optional[str] = Query(None, description="Trigram text search on patient name/email/phone"),
    has_reschedule_request: bool = Query(False),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    rr_join_cond = (
        (RescheduleRequest.appointment_id == Appointment.id)
        & (RescheduleRequest.status == RescheduleRequestStatus.PENDING)
    )
    base_filters = [Appointment.clinic_id == ctx.clinic_id]

    if patient_id is not None:
        base_filters.append(Appointment.patient_id == patient_id)
    if doctor_id is not None:
        base_filters.append(Appointment.doctor_id == doctor_id)
    if fecha is not None:
        day_start = datetime(fecha.year, fecha.month, fecha.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        base_filters.append(Appointment.starts_at >= day_start)
        base_filters.append(Appointment.starts_at < day_end)
    else:
        if start_date is not None:
            base_filters.append(
                Appointment.starts_at >= datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
            )
        if end_date is not None:
            base_filters.append(
                Appointment.starts_at < datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
            )
    if appt_status is not None:
        base_filters.append(Appointment.status == appt_status)
    if patient_q is not None and patient_q.strip():
        sub = select(Patient.id).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.search_text.ilike(f"%{patient_q.strip()}%"),
        )
        base_filters.append(Appointment.patient_id.in_(sub))
    if has_reschedule_request:
        base_filters.append(RescheduleRequest.id.isnot(None))

    count_q = (
        select(func.count(Appointment.id.distinct()))
        .outerjoin(RescheduleRequest, rr_join_cond)
        .where(*base_filters)
    )
    total = (await ctx.db.execute(count_q)).scalar_one()

    data_q = (
        select(Appointment, RescheduleRequest)
        .options(selectinload(Appointment.doctor).selectinload(Doctor.user))
        .outerjoin(RescheduleRequest, rr_join_cond)
        .where(*base_filters)
        .order_by(Appointment.starts_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await ctx.db.execute(data_q)).all()

    items = []
    for appt, rr in rows:
        resp = AppointmentWithRescheduleResponse.model_validate(appt)
        if appt.doctor and appt.doctor.user:
            resp.doctor_name = f"Dr. {_decrypt_name(appt.doctor.user.full_name_enc, '')}"
        resp.reschedule_request_id = rr.id if rr else None
        resp.reschedule_request_note = rr.patient_note if rr else None
        resp.reschedule_requested_at = rr.requested_at if rr else None
        items.append(resp)

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/agenda", response_model=List[AgendaResponse])
@limiter.limit("100/minute")
async def get_agenda(
    request: Request,
    ctx: TenantContext,
    _: AnyStaff,
    fecha: date = Query(..., description="Day to query (YYYY-MM-DD)"),
):
    day_start = datetime(fecha.year, fecha.month, fecha.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)

    stmt = (
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
        .where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.starts_at >= day_start,
            Appointment.starts_at < day_end,
        )
        .order_by(Appointment.starts_at)
    )
    result = await ctx.db.execute(stmt)
    return result.scalars().all()


@router.get("/range", response_model=List[AppointmentResponse])
@limiter.limit("100/minute")
async def get_appointments_range(
    request: Request,
    ctx: TenantContext,
    current_user: CurrentUser,
    start_date: datetime = Query(..., description="Range start (ISO datetime, UTC)"),
    end_date: datetime = Query(..., description="Range end (ISO datetime, UTC)"),
):
    if end_date <= start_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date must be after start_date.")
    if start_date.tzinfo is None:
        start_date = start_date.replace(tzinfo=timezone.utc)
    if end_date.tzinfo is None:
        end_date = end_date.replace(tzinfo=timezone.utc)
    if (end_date - start_date).days > 90:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Range cannot exceed 90 days.")

    q = select(Appointment).where(
        Appointment.clinic_id == ctx.clinic_id,
        Appointment.starts_at >= start_date,
        Appointment.starts_at < end_date,
    )

    if Role(current_user.role) == Role.DOCTOR:
        doctor_result = await ctx.db.execute(
            select(Doctor).where(Doctor.user_id == current_user.id)
        )
        doctor = doctor_result.scalar_one_or_none()
        if doctor is None:
            return []
        q = q.where(Appointment.doctor_id == doctor.id)

    q = q.order_by(Appointment.starts_at)
    result = await ctx.db.execute(q)
    return result.scalars().all()


# ── Export helpers ─────────────────────────────────────────────────────────────

_EXPORT_COLUMNS = [
    "ID", "Paciente", "Doctor", "Fecha", "Hora inicio", "Hora fin",
    "Estado", "Motivo", "Ubicación", "Notas",
]


def _appt_row(appt: Appointment) -> list:
    patient_name = _decrypt_name(appt.patient.full_name_enc if appt.patient else None, "")
    doctor_name = ""
    if appt.doctor and appt.doctor.user:
        doctor_name = _decrypt_name(appt.doctor.user.full_name_enc, "")
    return [
        str(appt.id),
        patient_name,
        doctor_name,
        appt.starts_at.date().isoformat(),
        appt.starts_at.strftime("%H:%M"),
        appt.ends_at.strftime("%H:%M"),
        appt.status,
        appt.reason or "",
        appt.location.name if appt.location else "",
        appt.notes_enc or "",
    ]


def _build_export(appointments, fmt: str) -> Response:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename = f"appointments_{today}"
    if fmt == "xlsx":
        import openpyxl  # noqa: PLC0415
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Citas"
        ws.append(_EXPORT_COLUMNS)
        for appt in appointments:
            ws.append(_appt_row(appt))
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            content=buf.read(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_EXPORT_COLUMNS)
    for appt in appointments:
        writer.writerow(_appt_row(appt))
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


# ── ICS helpers ───────────────────────────────────────────────────────────────

def _ics_escape(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
            .replace(",", "\\,")
            .replace(";", "\\;")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
    )


def _ics_fold(line: str) -> str:
    """Fold ICS content line at 75 octets per RFC 5545 §3.1."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts: list[str] = []
    remaining = line.encode("utf-8")
    max_bytes = 75
    while len(remaining) > max_bytes:
        split = max_bytes
        # Back off to avoid splitting a multi-byte UTF-8 sequence
        while split > 0 and (remaining[split - 1] & 0xC0) == 0x80:
            split -= 1
        if split == 0:
            split = max_bytes
        parts.append(remaining[:split].decode("utf-8"))
        remaining = remaining[split:]
        max_bytes = 74  # continuation lines start with 1 space
    parts.append(remaining.decode("utf-8"))
    return "\r\n ".join(parts)


def _dt_to_ics(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _appt_to_ics_event(appt: Appointment, dtstamp: str) -> dict:
    patient_name = _decrypt_name(
        appt.patient.full_name_enc if appt.patient else None, "Paciente"
    )
    doctor_name = ""
    if appt.doctor and appt.doctor.user:
        doctor_name = _decrypt_name(appt.doctor.user.full_name_enc, "Doctor")

    summary = f"Paciente: {patient_name}"
    if appt.reason:
        summary += f" - {appt.reason}"

    desc_parts: list[str] = []
    if doctor_name:
        desc_parts.append(f"Doctor: {doctor_name}")
    if appt.reason:
        desc_parts.append(f"Motivo: {appt.reason}")
    notes = _decrypt_name(appt.notes_enc, "")
    if notes:
        desc_parts.append(f"Notas: {notes}")

    location = appt.location.name if appt.location else ""
    url = ""
    if appt.magic_token:
        url = f"{settings.APP_BASE_URL.rstrip('/')}/appointments/public/{appt.magic_token}"

    return {
        "uid": f"{appt.id}@medsync",
        "dtstamp": dtstamp,
        "dtstart": _dt_to_ics(appt.starts_at),
        "dtend": _dt_to_ics(appt.ends_at),
        "summary": summary,
        "description": "\n".join(desc_parts),
        "location": location,
        "url": url,
    }


def _build_ics(events: list[dict]) -> str:
    """Build RFC 5545 iCalendar content from a list of event dicts."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Med-Sync//Med-Sync Calendar//ES",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for ev in events:
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:{ev['uid']}")
        lines.append(f"DTSTAMP:{ev['dtstamp']}")
        lines.append(f"DTSTART:{ev['dtstart']}")
        lines.append(f"DTEND:{ev['dtend']}")
        lines.append(_ics_fold(f"SUMMARY:{_ics_escape(ev['summary'])}"))
        if ev.get("description"):
            lines.append(_ics_fold(f"DESCRIPTION:{_ics_escape(ev['description'])}"))
        if ev.get("location"):
            lines.append(_ics_fold(f"LOCATION:{_ics_escape(ev['location'])}"))
        if ev.get("url"):
            lines.append(_ics_fold(f"URL:{ev['url']}"))
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


@router.get("/export")
@limiter.limit("10/minute", key_func=get_ip_and_clinic)
async def export_appointments(
    request: Request,
    ctx: TenantContext,
    current_user: CurrentUser,
    _: AnyStaff,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    doctor_id: Optional[uuid.UUID] = Query(None),
    appt_status: Optional[str] = Query(None, alias="status"),
    fmt: str = Query("csv", alias="format", pattern="^(csv|xlsx)$"),
):
    q = (
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.location),
        )
        .where(Appointment.clinic_id == ctx.clinic_id)
    )

    if Role(current_user.role) == Role.DOCTOR:
        dr_result = await ctx.db.execute(
            select(Doctor).where(
                Doctor.clinic_id == ctx.clinic_id,
                Doctor.user_id == current_user.id,
            )
        )
        doctor_profile = dr_result.scalar_one_or_none()
        if doctor_profile is None:
            return _build_export([], fmt)
        q = q.where(Appointment.doctor_id == doctor_profile.id)
    elif doctor_id is not None:
        q = q.where(Appointment.doctor_id == doctor_id)

    if start_date is not None:
        q = q.where(
            Appointment.starts_at >= datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
        )
    if end_date is not None:
        q = q.where(
            Appointment.starts_at < datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
        )
    if appt_status is not None:
        q = q.where(Appointment.status == appt_status)

    q = q.order_by(Appointment.starts_at)
    result = await ctx.db.execute(q)
    appointments = result.scalars().unique().all()
    return _build_export(appointments, fmt)


@router.get("/export/ics")
@limiter.limit("10/minute", key_func=get_ip_and_clinic)
async def export_appointments_ics(
    request: Request,
    ctx: TenantContext,
    current_user: CurrentUser,
    _: AnyStaff,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    doctor_id: Optional[uuid.UUID] = Query(None),
):
    """Export appointments as iCalendar (.ics). Doctors see only their own appointments."""
    now = datetime.now(timezone.utc)
    q = (
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.location),
        )
        .where(Appointment.clinic_id == ctx.clinic_id)
    )

    if Role(current_user.role) == Role.DOCTOR:
        dr_result = await ctx.db.execute(
            select(Doctor).where(
                Doctor.clinic_id == ctx.clinic_id,
                Doctor.user_id == current_user.id,
            )
        )
        doctor_profile = dr_result.scalar_one_or_none()
        if doctor_profile is None:
            return Response(
                content=_build_ics([]),
                media_type="text/calendar",
                headers={"Content-Disposition": 'attachment; filename="appointments.ics"'},
            )
        q = q.where(Appointment.doctor_id == doctor_profile.id)
    elif doctor_id is not None:
        q = q.where(Appointment.doctor_id == doctor_id)

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
        headers={"Content-Disposition": f'attachment; filename="appointments_{today}.ics"'},
    )


_ES_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _decrypt_name(value: str | None, fallback: str) -> str:
    """Decrypt an encrypted name field; if already plaintext, return as-is."""
    if not value:
        return fallback
    try:
        return decrypt(value)
    except Exception:
        return value


def _to_local(dt: datetime, tz_name: str) -> datetime:
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        tz = ZoneInfo("America/Mexico_City")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)


def _fmt_time(dt: datetime, use_12h: bool) -> str:
    if use_12h:
        period = "AM" if dt.hour < 12 else "PM"
        h12 = dt.hour % 12 or 12
        return f"{h12}:{dt.minute:02d} {period}"
    return f"{dt.hour}:{dt.minute:02d}"


def _format_appt_datetimes(
    starts_at: datetime, ends_at: datetime, tz_name: str
) -> tuple[str, str]:
    """Returns (formatted_date, formatted_time) in Spanish for the clinic timezone."""
    local_start = _to_local(starts_at, tz_name)
    local_end = _to_local(ends_at, tz_name)
    use_12h = tz_name.startswith("America/") or tz_name.startswith("Pacific/Honolulu")
    date_str = f"{local_start.day} de {_ES_MONTHS[local_start.month - 1]} de {local_start.year}"
    time_str = f"{_fmt_time(local_start, use_12h)} – {_fmt_time(local_end, use_12h)}"
    return date_str, time_str


async def _load_public_appt(token: str, db):
    """Load appointment with relationships; validate token & expiry."""
    result = await db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.clinic),
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
        .where(Appointment.magic_token == token)
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enlace inválido o expirado.")
    expires = appointment.magic_token_expires_at
    if expires is None or expires < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Este enlace ha expirado.")
    return appointment


def _public_response(appt: Appointment, tz_name: str = "America/Mexico_City") -> PublicAppointmentResponse:
    patient_name = _decrypt_name(
        appt.patient.full_name_enc if appt.patient else None, "Paciente"
    )
    doctor_name = "Doctor"
    if appt.doctor and appt.doctor.user:
        doctor_name = _decrypt_name(appt.doctor.user.full_name_enc, "Doctor")
    formatted_date, formatted_time = _format_appt_datetimes(appt.starts_at, appt.ends_at, tz_name)
    starts = appt.starts_at if appt.starts_at.tzinfo else appt.starts_at.replace(tzinfo=timezone.utc)
    can_cancel = datetime.now(timezone.utc) + timedelta(hours=settings.PATIENT_CANCEL_HOURS_BEFORE) <= starts
    return PublicAppointmentResponse(
        id=appt.id,
        doctor_id=appt.doctor_id,
        starts_at=appt.starts_at,
        ends_at=appt.ends_at,
        status=appt.status,
        reason=appt.reason,
        patient_name=patient_name,
        doctor_name=doctor_name,
        formatted_date=formatted_date,
        formatted_time=formatted_time,
        can_cancel=can_cancel,
        patient_confirmed_at=appt.patient_confirmed_at,
    )


_DAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@router.get("/public/slots", response_model=SlotsResponse)
@limiter.limit("100/minute")
async def get_public_slots(
    request: Request,
    db: DBSession,
    doctor_id: uuid.UUID = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    # Cap range to 3 months
    max_end = start_date + timedelta(days=92)
    if end_date > max_end:
        end_date = max_end
    if end_date < start_date:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_date debe ser posterior a start_date.")

    result = await db.execute(
        select(Doctor).where(
            Doctor.id == doctor_id,
            Doctor.is_accepting_patients.is_(True),
        )
    )
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor no encontrado.")

    range_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    range_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)

    result = await db.execute(
        select(Appointment).where(
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < range_end,
            Appointment.ends_at > range_start,
        )
    )
    booked = [(a.starts_at, a.ends_at) for a in result.scalars().all()]

    duration = timedelta(minutes=max(doctor.appointment_duration_minutes, 1))
    working_hours = doctor.working_hours or {}

    slots: list[SlotItem] = []
    current = start_date
    while current <= end_date:
        day_schedule = working_hours.get(_DAY_ABBR[current.weekday()], [])
        for block in day_schedule:
            try:
                bsh, bsm = map(int, block["start"].split(":"))
                beh, bem = map(int, block["end"].split(":"))
            except (KeyError, ValueError):
                continue
            slot_start = datetime(current.year, current.month, current.day, bsh, bsm, tzinfo=timezone.utc)
            block_end = datetime(current.year, current.month, current.day, beh, bem, tzinfo=timezone.utc)
            while slot_start + duration <= block_end:
                slot_end = slot_start + duration
                if not any(bs < slot_end and be > slot_start for bs, be in booked):
                    slots.append(SlotItem(starts_at=slot_start, ends_at=slot_end))
                slot_start = slot_end
        current += timedelta(days=1)

    return SlotsResponse(doctor_id=doctor_id, slots=slots)


@router.get("/public/{token}", response_model=PublicAppointmentResponse)
@limiter.limit("60/minute")
async def get_appointment_public(request: Request, token: str, db: DBSession):
    appt = await _load_public_appt(token, db)
    tz_name = (appt.clinic.timezone if appt.clinic else None) or "America/Mexico_City"
    return _public_response(appt, tz_name)


_ACTIONABLE = {AppointmentStatus.SCHEDULED}


@router.put("/public/{token}", response_model=PublicAppointmentResponse)
@limiter.limit("10/minute")
async def update_appointment_public(
    request: Request,
    token: str,
    body: PublicAppointmentAction,
    db: DBSession,
):
    appt = await _load_public_appt(token, db)
    if appt.status not in _ACTIONABLE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Esta cita no puede modificarse. Estado actual: {appt.status}",
        )
    # Cache relationship data before commit (commit expires lazy-loaded attrs)
    patient_name = _decrypt_name(
        appt.patient.full_name_enc if appt.patient else None, "Paciente"
    )
    doctor_name = "Doctor"
    if appt.doctor and appt.doctor.user:
        doctor_name = _decrypt_name(appt.doctor.user.full_name_enc, "Doctor")
    doctor_id = appt.doctor_id
    tz_name = (appt.clinic.timezone if appt.clinic else None) or "America/Mexico_City"

    if body.action == "reschedule":
        conflict_q = select(Appointment).where(
            Appointment.clinic_id == appt.clinic_id,
            Appointment.doctor_id == appt.doctor_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < body.new_ends_at,
            Appointment.ends_at > body.new_starts_at,
            Appointment.id != appt.id,
        )
        conflict_result = await db.execute(conflict_q)
        if conflict_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El horario seleccionado ya está ocupado. Por favor elige otro.",
            )
        appt.starts_at = body.new_starts_at
        appt.ends_at = body.new_ends_at
        appt.status = AppointmentStatus.SCHEDULED
    elif body.action == "cancel":
        starts = appt.starts_at if appt.starts_at.tzinfo else appt.starts_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) + timedelta(hours=settings.PATIENT_CANCEL_HOURS_BEFORE) > starts:
            h = settings.PATIENT_CANCEL_HOURS_BEFORE
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"No es posible cancelar con menos de {h} hora{'s' if h != 1 else ''} de anticipación.",
            )
        appt.status = AppointmentStatus.CANCELED_BY_PATIENT
    else:
        appt.status = AppointmentStatus.SCHEDULED

    await db.commit()
    await db.refresh(appt)
    formatted_date, formatted_time = _format_appt_datetimes(appt.starts_at, appt.ends_at, tz_name)
    return PublicAppointmentResponse(
        id=appt.id,
        doctor_id=doctor_id,
        starts_at=appt.starts_at,
        ends_at=appt.ends_at,
        status=appt.status,
        reason=appt.reason,
        patient_name=patient_name,
        doctor_name=doctor_name,
        formatted_date=formatted_date,
        formatted_time=formatted_time,
    )


@router.post("/public/confirm/{token}")
@limiter.limit("10/minute")
async def confirm_attendance_public(
    request: Request,
    token: str,
    db: DBSession,
):
    appt = await _load_public_appt(token, db)

    if appt.patient_confirmed_at is not None:
        return {
            "message": "Cita confirmada",
            "confirmed_at": appt.patient_confirmed_at.isoformat(),
        }

    now = datetime.now(timezone.utc)
    appt.patient_confirmed_at = now
    appt.patient_confirmation_channel = "magic_link"
    await db.commit()

    return {
        "message": "Cita confirmada",
        "confirmed_at": now.isoformat(),
    }


@router.post("/public/reschedule-request/{token}", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def request_reschedule_public(
    request: Request,
    token: str,
    body: RescheduleRequestPublicBody,
    db: DBSession,
    background_tasks: BackgroundTasks,
):
    appt = await _load_public_appt(token, db)

    starts = appt.starts_at if appt.starts_at.tzinfo else appt.starts_at.replace(tzinfo=timezone.utc)
    if starts <= datetime.now(timezone.utc):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se puede solicitar reagendar una cita que ya ocurrió.",
        )

    req = RescheduleRequest(
        clinic_id=appt.clinic_id,
        appointment_id=appt.id,
        patient_note=body.note,
        status=RescheduleRequestStatus.PENDING,
        requested_at=datetime.now(timezone.utc),
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)

    patient_name = _decrypt_name(appt.patient.full_name_enc if appt.patient else None, "Paciente")
    doctor_name = "Doctor"
    if appt.doctor and appt.doctor.user:
        doctor_name = _decrypt_name(appt.doctor.user.full_name_enc, "Doctor")

    send_reschedule_request_notification(
        notify_email=settings.CLINIC_NOTIFY_EMAIL,
        patient_name=patient_name,
        doctor_name=doctor_name,
        starts_at=appt.starts_at,
        patient_note=body.note,
        background_tasks=background_tasks,
    )

    return {"message": "Solicitud enviada", "request_id": str(req.id)}


async def _load_reschedule_appt(token: str, db):
    result = await db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.clinic),
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
        .where(Appointment.reschedule_token == token)
    )
    appt = result.scalar_one_or_none()
    if appt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Enlace inválido o expirado.")
    if appt.reschedule_token_expires_at is None or appt.reschedule_token_expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Este enlace ha expirado.")
    if appt.proposed_starts_at is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Esta cita ya no tiene un cambio pendiente de confirmación.",
        )
    return appt


@router.get("/public/reschedule/{token}/confirm")
@limiter.limit("10/minute")
async def confirm_reschedule(request: Request, token: str, db: DBSession):
    appt = await _load_reschedule_appt(token, db)

    conflict_result = await db.execute(
        select(Appointment).where(
            Appointment.clinic_id == appt.clinic_id,
            Appointment.doctor_id == appt.doctor_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < appt.proposed_ends_at,
            Appointment.ends_at > appt.proposed_starts_at,
            Appointment.id != appt.id,
        )
    )
    if conflict_result.scalar_one_or_none() is not None:
        appt.proposed_starts_at = None
        appt.proposed_ends_at = None
        appt.reschedule_token = None
        appt.reschedule_token_expires_at = None
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El horario propuesto ya no está disponible. Por favor, solicita un nuevo cambio de cita.",
        )

    appt.starts_at = appt.proposed_starts_at
    appt.ends_at = appt.proposed_ends_at
    appt.status = AppointmentStatus.SCHEDULED
    appt.proposed_starts_at = None
    appt.proposed_ends_at = None
    appt.reschedule_token = None
    appt.reschedule_token_expires_at = None
    await db.commit()
    return {"message": "Cambio de cita confirmado exitosamente."}


@router.get("/public/reschedule/{token}/reject")
@limiter.limit("10/minute")
async def reject_reschedule(request: Request, token: str, db: DBSession):
    appt = await _load_reschedule_appt(token, db)
    appt.status = AppointmentStatus.SCHEDULED
    appt.proposed_starts_at = None
    appt.proposed_ends_at = None
    appt.reschedule_token = None
    appt.reschedule_token_expires_at = None
    await db.commit()
    return {"message": "Cambio de cita rechazado. Tu cita se mantiene en el horario original."}


_STATUS_TRANSITIONS: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.SCHEDULED: frozenset({
        AppointmentStatus.COMPLETED,
        AppointmentStatus.CANCELED,
    }),
}


@router.patch("/{appointment_id}/status", response_model=AppointmentResponse)
@limiter.limit("100/minute")
async def patch_appointment_status(
    request: Request,
    appointment_id: uuid.UUID,
    body: AppointmentStatusPatch,
    ctx: TenantContext,
    _: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    current = AppointmentStatus(appointment.status)
    target = AppointmentStatus(body.status)
    allowed = _STATUS_TRANSITIONS.get(current, frozenset())

    if target not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot transition '{current}' → '{target}'. "
                f"Allowed: {[s.value for s in allowed] or ['none — terminal state']}."
            ),
        )

    appointment.status = target
    await ctx.db.commit()
    await ctx.db.refresh(appointment)
    return appointment


@router.patch("/{appointment_id}/mark-no-show", response_model=AppointmentResponse)
@limiter.limit("100/minute")
async def mark_no_show(
    request: Request,
    appointment_id: uuid.UUID,
    ctx: TenantContext,
    _: DoctorOrAbove,
):
    appt_result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = appt_result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    if appointment.status != AppointmentStatus.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Solo se pueden marcar como no-show las citas con estado 'scheduled'. Estado actual: '{appointment.status}'.",
        )

    patient_result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == appointment.patient_id,
        )
    )
    patient = patient_result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    appointment.status = AppointmentStatus.NO_SHOW
    appointment.was_no_show = True

    if patient is not None:
        patient.no_show_count = (patient.no_show_count or 0) + 1
        patient.last_no_show_at = now

    await ctx.db.commit()
    await ctx.db.refresh(appointment)
    return appointment


@router.get("/{appointment_id}", response_model=AppointmentResponse)
@limiter.limit("100/minute")
async def get_appointment(
    request: Request,
    appointment_id: uuid.UUID,
    ctx: TenantContext,
    _: AnyStaff,
):
    result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")
    return appointment


@router.put("/{appointment_id}", response_model=AppointmentResponse)
@limiter.limit("100/minute")
async def update_appointment(
    request: Request,
    appointment_id: uuid.UUID,
    body: AppointmentUpdate,
    ctx: TenantContext,
    _: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    updates = body.model_dump(exclude_unset=True)

    new_starts = updates.get("starts_at", appointment.starts_at)
    new_ends = updates.get("ends_at", appointment.ends_at)

    if "starts_at" in updates or "ends_at" in updates:
        await _assert_no_overlap(ctx, appointment.doctor_id, new_starts, new_ends, exclude_id=appointment_id)

    for field, value in updates.items():
        if field == "notes":
            appointment.notes_enc = value
        else:
            setattr(appointment, field, value)

    await ctx.db.commit()
    await ctx.db.refresh(appointment)
    return appointment


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("100/minute")
async def cancel_appointment(
    request: Request,
    appointment_id: uuid.UUID,
    ctx: TenantContext,
    _: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    appointment.status = AppointmentStatus.CANCELED
    await ctx.db.commit()


@router.get("/{appointment_id}/notes", response_model=List[AppointmentNoteResponse])
@limiter.limit("200/minute")
async def list_appointment_notes(
    request: Request,
    appointment_id: uuid.UUID,
    ctx: TenantContext,
    _: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(AppointmentNote)
        .options(selectinload(AppointmentNote.created_by))
        .where(
            AppointmentNote.clinic_id == ctx.clinic_id,
            AppointmentNote.appointment_id == appointment_id,
        )
        .order_by(AppointmentNote.created_at)
    )
    notes = result.scalars().all()
    out = []
    for n in notes:
        resp = AppointmentNoteResponse.model_validate(n)
        if n.created_by:
            resp.author_name = _decrypt_name(n.created_by.full_name_enc, "Usuario")
        out.append(resp)
    return out


@router.post("/{appointment_id}/notes", response_model=AppointmentNoteResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
async def create_appointment_note(
    request: Request,
    appointment_id: uuid.UUID,
    body: AppointmentNoteCreate,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    appt_exists = await ctx.db.execute(
        select(Appointment.id).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    if appt_exists.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    note = AppointmentNote(
        clinic_id=ctx.clinic_id,
        appointment_id=appointment_id,
        content=body.content.strip(),
        created_by_id=current_user.id,
    )
    ctx.db.add(note)
    await ctx.db.commit()
    await ctx.db.refresh(note)

    resp = AppointmentNoteResponse.model_validate(note)
    resp.author_name = _decrypt_name(current_user.full_name_enc, "Usuario")
    return resp


@router.delete("/{appointment_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("100/minute")
async def delete_appointment_note(
    request: Request,
    appointment_id: uuid.UUID,
    note_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(AppointmentNote).where(
            AppointmentNote.clinic_id == ctx.clinic_id,
            AppointmentNote.appointment_id == appointment_id,
            AppointmentNote.id == note_id,
        )
    )
    note = result.scalar_one_or_none()
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found.")

    user_role = Role(current_user.role)
    if user_role not in (Role.OWNER, Role.ASSISTANT) and note.created_by_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puedes eliminar tus propias notas.",
        )

    await ctx.db.delete(note)
    await ctx.db.commit()


@router.post("/{appointment_id}/propose-reschedule", response_model=AppointmentResponse)
@limiter.limit("30/minute")
async def propose_reschedule(
    request: Request,
    appointment_id: uuid.UUID,
    body: ProposeRescheduleBody,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    background_tasks: BackgroundTasks,
):
    result = await ctx.db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
        .where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    if appointment.status != AppointmentStatus.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot propose reschedule for appointment in status '{appointment.status}'.",
        )

    await _assert_no_overlap(ctx, appointment.doctor_id, body.starts_at, body.ends_at, exclude_id=appointment_id)

    token = str(uuid.uuid4())
    appointment.proposed_starts_at = body.starts_at
    appointment.proposed_ends_at = body.ends_at
    appointment.reschedule_token = token
    appointment.reschedule_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
    # Status stays SCHEDULED; proposed times stored in proposed_starts_at/ends_at

    await ctx.db.commit()
    await ctx.db.refresh(appointment)

    base = settings.APP_BASE_URL.rstrip("/")
    confirm_url = f"{base}/appointments/public/reschedule/{token}/confirm"
    reject_url = f"{base}/appointments/public/reschedule/{token}/reject"

    patient = appointment.patient
    patient_email = patient.email_enc or "" if patient else ""
    patient_name = _decrypt_name(patient.full_name_enc if patient else None, "Paciente")
    doctor_name = "Doctor"
    if appointment.doctor and appointment.doctor.user:
        doctor_name = _decrypt_name(appointment.doctor.user.full_name_enc, "Doctor")

    send_reschedule_proposal(
        patient_email=patient_email,
        patient_name=patient_name,
        doctor_name=doctor_name,
        proposed_starts_at=body.starts_at,
        proposed_ends_at=body.ends_at,
        confirm_url=confirm_url,
        reject_url=reject_url,
        background_tasks=background_tasks,
    )

    return appointment


@router.post("/{appointment_id}/attach-pdf", response_model=MedicalRecordResponse)
@limiter.limit("10/minute")
async def attach_pdf_to_appointment(
    request: Request,
    appointment_id: uuid.UUID,
    ctx: TenantContext,
    _: DoctorOrAbove,
    file: UploadFile = File(...),
):
    result = await ctx.db.execute(
        select(Appointment).where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.id == appointment_id,
        )
    )
    appointment = result.scalar_one_or_none()
    if appointment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appointment not found.")

    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only PDF files are accepted (application/pdf).",
        )
    content = await file.read()
    if len(content) > _MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds the 10 MB limit.",
        )

    rec_result = await ctx.db.execute(
        select(MedicalRecord).where(
            MedicalRecord.clinic_id == ctx.clinic_id,
            MedicalRecord.appointment_id == appointment_id,
        )
    )
    record = rec_result.scalar_one_or_none()

    if record is None:
        record = MedicalRecord(
            clinic_id=ctx.clinic_id,
            patient_id=appointment.patient_id,
            doctor_id=appointment.doctor_id,
            appointment_id=appointment.id,
            key_version=_key_version(),
        )
        ctx.db.add(record)
        await ctx.db.flush()

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S%f")
    upload_dir = Path("uploads") / "medical-records" / str(ctx.clinic_id) / str(record.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{timestamp}.pdf"
    dest.write_bytes(content)

    record.s3_pdf_key = dest.as_posix()
    await ctx.db.commit()
    await ctx.db.refresh(record)
    return record
