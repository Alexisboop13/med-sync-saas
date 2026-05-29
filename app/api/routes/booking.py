"""
app/api/routes/booking.py
──────────────────────────────────────────────────────────────────────────────
Public self-booking endpoints — no JWT required.

URL pattern: /api/v1/public/book/{clinic_slug}/...

Endpoints:
  GET  /public/book/{slug}/info        → clinic name + accepting doctors list
  GET  /public/book/{slug}/slots       → available slots for a doctor (7-day max)
  POST /public/book/{slug}             → create appointment (find-or-create patient)

Security notes:
  • clinic_slug is always validated against the DB; suspended/inactive clinics 404.
  • Patient lookup by phone_search_hash (HMAC — never exposes raw phone).
  • Overlap checked at service layer before commit; DB GIST constraint is the
    race-condition guard.
  • Magic token is a random UUID stored in plain text for MVP (see appointment.py
    rationale); short-lived (72 h).
  • POST is rate-limited to 10/min per IP to prevent appointment spam.
"""

from __future__ import annotations

import random
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

import pytz

from app.core.config import settings
from app.core.crypto import _load_keyring, make_search_hash
from app.core.email import send_booking_confirmation
from app.core.limiter import limiter
from app.core.whatsapp import format_appointment_confirmation, send_whatsapp
from app.db.session import get_db
from app.models.appointment import ACTIVE_STATUSES, Appointment, AppointmentStatus
from app.models.clinic import Clinic
from app.models.doctor import Doctor
from app.models.email_verification import EmailVerification
from app.models.patient import Patient
from app.schemas.appointment import SlotItem, SlotsResponse

router = APIRouter(tags=["Public Booking"])

_DAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"
_SAFE_DIGITS = "23456789"

DBSession = Annotated[object, Depends(get_db)]


# ── Response schemas ───────────────────────────────────────────────────────────

class DoctorPublicInfo(BaseModel):
    id: uuid.UUID
    name: str
    title: str
    specialty: str
    bio: Optional[str] = None
    duration_minutes: int


class ClinicPublicInfo(BaseModel):
    clinic_name: str
    slug: str
    timezone: str
    doctors: List[DoctorPublicInfo]


class BookingRequest(BaseModel):
    doctor_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    patient_name: str = Field(..., min_length=2, max_length=200)
    patient_email: str = Field(..., max_length=200)
    patient_phone: str = Field(..., min_length=7, max_length=30)
    reason: Optional[str] = Field(None, max_length=300)
    verification_token: str = Field(..., description="Token from POST /public/verify/check-code")

    @field_validator("patient_email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("Email inválido.")
        return v

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def ensure_utc(cls, v: object) -> object:
        if isinstance(v, datetime) and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    @field_validator("ends_at")
    @classmethod
    def ends_after_starts(cls, v: datetime, info) -> datetime:
        starts = info.data.get("starts_at")
        if starts and v <= starts:
            raise ValueError("ends_at debe ser posterior a starts_at.")
        return v


class BookingResponse(BaseModel):
    appointment_id: uuid.UUID
    message: str
    magic_link_url: str


# ── Internal helpers ───────────────────────────────────────────────────────────

async def _get_active_clinic(slug: str, db) -> Clinic:
    result = await db.execute(
        select(Clinic).where(Clinic.slug == slug, Clinic.is_active.is_(True))
    )
    clinic = result.scalar_one_or_none()
    if clinic is None or clinic.is_suspended:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clínica no encontrada.")
    return clinic


def _current_key_version() -> int:
    return max(_load_keyring())


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone)


async def _generate_medical_code(db, clinic_id: uuid.UUID) -> str:
    for _ in range(10):
        code = (
            random.choice(_CONSONANTS)
            + random.choice(_CONSONANTS)
            + random.choice(_SAFE_DIGITS)
            + random.choice(_SAFE_DIGITS)
        )
        existing = (await db.execute(
            select(Patient.id).where(
                Patient.clinic_id == clinic_id,
                Patient.medical_record_code == code,
            )
        )).scalar_one_or_none()
        if existing is None:
            return code
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="No se pudo generar el código de paciente. Intenta de nuevo.",
    )


async def _find_or_create_patient(
    db,
    clinic_id: uuid.UUID,
    name: str,
    email: str,
    phone: str,
) -> tuple[Patient, bool]:
    """
    Look up patient by phone_search_hash within the clinic.
    Returns (patient, was_existing). Creates a new patient record if not found.
    """
    digits = _normalize_phone(phone)
    phone_hash = make_search_hash(digits) if digits else None

    if phone_hash:
        result = await db.execute(
            select(Patient).where(
                Patient.clinic_id == clinic_id,
                Patient.phone_search_hash == phone_hash,
                Patient.is_active.is_(True),
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            return existing, True

    name_stripped = name.strip()
    code = await _generate_medical_code(db, clinic_id)
    search_text = f"{name_stripped.lower()} {email.lower()} {digits}".strip()

    patient = Patient(
        clinic_id=clinic_id,
        full_name_enc=name_stripped,
        full_name_search_hash=make_search_hash(name_stripped.lower()),
        phone_enc=phone or None,
        phone_search_hash=phone_hash,
        email_enc=email or None,
        medical_record_code=code,
        search_text=search_text,
        key_version=_current_key_version(),
    )
    db.add(patient)
    await db.flush()
    return patient, False


def _decrypt_name(value: str | None, fallback: str) -> str:
    if not value:
        return fallback
    from app.core.crypto import decrypt  # noqa: PLC0415
    try:
        return decrypt(value)
    except Exception:
        return value


async def _assert_no_overlap(
    db,
    clinic_id: uuid.UUID,
    doctor_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
) -> None:
    result = await db.execute(
        select(Appointment).where(
            Appointment.clinic_id == clinic_id,
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < ends_at,
            Appointment.ends_at > starts_at,
        )
    )
    conflict = result.scalar_one_or_none()
    if conflict is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El horario seleccionado ya está ocupado. Por favor elige otro.",
        )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/public/book/{clinic_slug}/info", response_model=ClinicPublicInfo)
@limiter.limit("60/minute")
async def get_booking_info(
    request: Request,
    clinic_slug: str,
    db: DBSession,
):
    """Public endpoint: clinic name + list of accepting doctors."""
    clinic = await _get_active_clinic(clinic_slug, db)

    result = await db.execute(
        select(Doctor)
        .options(selectinload(Doctor.user))
        .where(
            Doctor.clinic_id == clinic.id,
            Doctor.is_accepting_patients.is_(True),
        )
        .order_by(Doctor.specialty)
    )
    doctors = result.scalars().all()

    doctor_list: List[DoctorPublicInfo] = []
    for doc in doctors:
        name = _decrypt_name(doc.user.full_name_enc if doc.user else None, "Doctor")
        doctor_list.append(
            DoctorPublicInfo(
                id=doc.id,
                name=name,
                title=doc.title,
                specialty=doc.specialty,
                bio=doc.bio,
                duration_minutes=doc.appointment_duration_minutes,
            )
        )

    return ClinicPublicInfo(
        clinic_name=clinic.name,
        slug=clinic.slug,
        timezone=clinic.timezone,
        doctors=doctor_list,
    )


@router.get("/public/book/{clinic_slug}/slots", response_model=SlotsResponse)
@limiter.limit("60/minute")
async def get_booking_slots(
    request: Request,
    clinic_slug: str,
    db: DBSession,
    doctor_id: uuid.UUID = Query(...),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    """Available time slots for a specific doctor (max 7 days)."""
    clinic = await _get_active_clinic(clinic_slug, db)

    # Hard cap at 7 days
    max_end = start_date + timedelta(days=6)
    if end_date > max_end:
        end_date = max_end
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="end_date debe ser posterior a start_date.")

    result = await db.execute(
        select(Doctor).where(
            Doctor.clinic_id == clinic.id,
            Doctor.id == doctor_id,
            Doctor.is_accepting_patients.is_(True),
        )
    )
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor no encontrado.")

    range_start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
    range_end = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)

    booked_result = await db.execute(
        select(Appointment.starts_at, Appointment.ends_at).where(
            Appointment.clinic_id == clinic.id,
            Appointment.doctor_id == doctor_id,
            Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
            Appointment.starts_at < range_end,
            Appointment.ends_at > range_start,
        )
    )
    booked = [(r.starts_at, r.ends_at) for r in booked_result.all()]

    duration = timedelta(minutes=max(doctor.appointment_duration_minutes, 1))
    working_hours = doctor.working_hours or {}
    now = datetime.now(timezone.utc)

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
                # Skip slots in the past (add 15-min buffer)
                if slot_start > now + timedelta(minutes=15):
                    if not any(bs < slot_end and be > slot_start for bs, be in booked):
                        slots.append(SlotItem(starts_at=slot_start, ends_at=slot_end))
                slot_start = slot_end
        current += timedelta(days=1)

    return SlotsResponse(doctor_id=doctor_id, slots=slots)


@router.post(
    "/public/book/{clinic_slug}",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
async def create_public_booking(
    request: Request,
    clinic_slug: str,
    body: BookingRequest,
    background_tasks: BackgroundTasks,
    db: DBSession,
):
    """
    Self-booking endpoint. Creates or reuses the patient, creates the appointment,
    and sends a magic-link confirmation email.

    Rate-limited to 10/min per IP to prevent spam.
    """
    clinic = await _get_active_clinic(clinic_slug, db)

    # Validate doctor belongs to this clinic and is accepting patients
    result = await db.execute(
        select(Doctor)
        .options(selectinload(Doctor.user))
        .where(
            Doctor.clinic_id == clinic.id,
            Doctor.id == body.doctor_id,
            Doctor.is_accepting_patients.is_(True),
        )
    )
    doctor = result.scalar_one_or_none()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor no encontrado.")

    # Validate email verification token
    now = datetime.now(timezone.utc)
    ev_result = await db.execute(
        select(EmailVerification).where(
            EmailVerification.verification_token == body.verification_token
        )
    )
    ev = ev_result.scalar_one_or_none()
    if ev is None or ev.verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Token de verificación inválido. Verifica tu correo antes de continuar.",
        )
    token_exp = ev.token_expires_at
    if token_exp is not None and token_exp.tzinfo is None:
        token_exp = token_exp.replace(tzinfo=timezone.utc)
    if token_exp is None or token_exp < now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El token de verificación ha expirado. Por favor verifica tu correo nuevamente.",
        )
    expected_hash = make_search_hash(body.patient_email.lower().strip())
    if ev.email_hash != expected_hash:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="El correo no coincide con el token de verificación.",
        )

    # Validate appointment is in the future
    if body.starts_at <= now:
        raise HTTPException(status_code=422, detail="La cita debe ser en el futuro.")

    # Validate time slot is within doctor's working hours
    _validate_working_hours(doctor, body.starts_at, body.ends_at)

    # Overlap check (service layer guard)
    await _assert_no_overlap(db, clinic.id, body.doctor_id, body.starts_at, body.ends_at)

    # Find or create patient
    patient, was_existing = await _find_or_create_patient(
        db=db,
        clinic_id=clinic.id,
        name=body.patient_name,
        email=body.patient_email,
        phone=body.patient_phone,
    )

    if was_existing and (patient.no_show_count or 0) >= 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Debes contactar a la clínica directamente para agendar tu cita.",
        )

    # Create appointment
    magic_token = str(uuid.uuid4())
    appointment = Appointment(
        clinic_id=clinic.id,
        doctor_id=body.doctor_id,
        patient_id=patient.id,
        created_by_id=None,  # self-booked
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        status=AppointmentStatus.SCHEDULED,
        reason=body.reason,
        magic_token=magic_token,
        magic_token_expires_at=now + timedelta(hours=72),
        key_version=_current_key_version(),
    )
    db.add(appointment)
    ev.token_expires_at = now  # single-use: invalidate immediately
    await db.commit()
    await db.refresh(appointment)

    magic_link_url = f"{settings.APP_BASE_URL}/appointments/public/{magic_token}"
    doctor_name = _decrypt_name(
        doctor.user.full_name_enc if doctor.user else None, "Doctor"
    )

    send_booking_confirmation(
        patient_email=body.patient_email,
        patient_name=body.patient_name,
        doctor_name=doctor_name,
        clinic_name=clinic.name,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        reason=body.reason,
        magic_link_url=magic_link_url,
        background_tasks=background_tasks,
    )

    # WhatsApp confirmation (si GreenAPI está configurado)
    if body.patient_phone and settings.GREENAPI_INSTANCE_ID:
        try:
            tz = pytz.timezone(clinic.timezone or "America/Mexico_City")
            starts_local = body.starts_at.astimezone(tz).strftime("%A %d de %B %Y a las %H:%M")
        except Exception:
            starts_local = body.starts_at.strftime("%Y-%m-%d %H:%M UTC")
        wa_msg = format_appointment_confirmation(
            patient_name=body.patient_name,
            doctor_name=doctor_name,
            clinic_name=clinic.name,
            starts_at_local=starts_local,
            magic_link=magic_link_url,
        )
        background_tasks.add_task(send_whatsapp, body.patient_phone, wa_msg)

    return BookingResponse(
        appointment_id=appointment.id,
        message="Cita agendada exitosamente. Revisa tu correo para confirmar.",
        magic_link_url=magic_link_url,
    )


# ── Working-hours validator ────────────────────────────────────────────────────

def _validate_working_hours(doctor: Doctor, starts_at: datetime, ends_at: datetime) -> None:
    """Raise 422 if the requested slot falls outside the doctor's working_hours."""
    working_hours = doctor.working_hours or {}
    day_key = _DAY_ABBR[starts_at.weekday()]
    schedule = working_hours.get(day_key, [])

    if not schedule:
        day_names = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
        raise HTTPException(
            status_code=422,
            detail=(
                f"El doctor no atiende los {day_names[starts_at.weekday()]}. "
                "Por favor selecciona otro día."
            ),
        )

    slot_start_minutes = starts_at.hour * 60 + starts_at.minute
    slot_end_minutes = ends_at.hour * 60 + ends_at.minute

    for block in schedule:
        try:
            bsh, bsm = map(int, block["start"].split(":"))
            beh, bem = map(int, block["end"].split(":"))
            block_start = bsh * 60 + bsm
            block_end = beh * 60 + bem
            if block_start <= slot_start_minutes and slot_end_minutes <= block_end:
                return
        except (KeyError, ValueError):
            continue

    schedule_str = ", ".join(
        f"{b.get('start', '?')}–{b.get('end', '?')}"
        for b in schedule
        if isinstance(b, dict)
    )
    raise HTTPException(
        status_code=422,
        detail=(
            f"El horario solicitado está fuera del horario del doctor. "
            f"Horario disponible ese día: {schedule_str}."
        ),
    )
