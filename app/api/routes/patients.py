from __future__ import annotations

import random
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload

from app.api.deps import AnyStaff, CurrentUser, DoctorOrAbove, OwnerOnly, Role, TenantContext
from app.core.limiter import get_ip_and_clinic, limiter
from app.core.crypto import _load_keyring, make_search_hash
from app.models.audit_log import EventType
from app.models.appointment import Appointment
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.schemas.patient import PatientCreate, PatientResponse, PatientUpdate, PaginatedPatientResponse
from app.services.audit import log_audit

router = APIRouter(prefix="/patients", tags=["Patients"])

_CONSONANTS = "BCDFGHJKLMNPQRSTVWXYZ"
_SAFE_DIGITS = "23456789"


async def _generate_patient_code(db, clinic_id: uuid.UUID) -> str:
    for _ in range(5):
        code = (
            random.choice(_CONSONANTS)
            + random.choice(_CONSONANTS)
            + random.choice(_SAFE_DIGITS)
            + random.choice(_SAFE_DIGITS)
        )
        result = await db.execute(
            select(Patient).where(
                Patient.clinic_id == clinic_id,
                Patient.medical_record_code == code,
            )
        )
        if result.scalar_one_or_none() is None:
            return code
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Could not generate unique patient code. Try again.",
    )


_ENC_FIELD_MAP = {
    "full_name": "full_name_enc",
    "phone": "phone_enc",
    "email": "email_enc",
    "date_of_birth": "date_of_birth_enc",
    "gender": "gender_enc",
    "address": "address_enc",
    "blood_type": "blood_type_enc",
    "allergies": "allergies_enc",
    "notes": "notes_enc",
    "emergency_contact_name": "emergency_contact_name_enc",
    "emergency_contact_phone": "emergency_contact_phone_enc",
}


def _key_version() -> int:
    ring = _load_keyring()
    return max(ring)


def _digits(phone: str) -> str:
    return re.sub(r"\D", "", phone)


def _build_search_text(full_name: str, email: str | None, phone: str | None) -> str:
    return " ".join(filter(None, [full_name, email, phone])).lower()


@router.post("", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
async def create_patient(
    request: Request,
    body: PatientCreate,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    patient = Patient(
        clinic_id=ctx.clinic_id,
        medical_record_code=await _generate_patient_code(ctx.db, ctx.clinic_id),
        full_name_enc=body.full_name,
        full_name_search_hash=make_search_hash(body.full_name.lower().strip()),
        phone_enc=body.phone,
        phone_search_hash=make_search_hash(
            _digits(body.phone)) if body.phone else None,
        email_enc=body.email,
        date_of_birth_enc=body.date_of_birth,
        gender_enc=body.gender,
        address_enc=body.address,
        blood_type_enc=body.blood_type,
        allergies_enc=body.allergies,
        notes_enc=body.notes,
        emergency_contact_name_enc=body.emergency_contact_name,
        emergency_contact_phone_enc=body.emergency_contact_phone,
        search_text=_build_search_text(body.full_name, body.email, body.phone),
        key_version=_key_version(),
    )
    ctx.db.add(patient)
    await ctx.db.flush()
    await log_audit(
        ctx.db,
        event_type=EventType.PATIENT_CREATED,
        entity_type="Patient",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=patient.id,
        source=request.url.path,
        data={
            "actor_role": current_user.role,
            "medical_record_code": patient.medical_record_code,
            "masked_fields": ["full_name_enc", "phone_enc", "email_enc", "date_of_birth_enc"],
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()
    await ctx.db.refresh(patient)
    return patient


@router.get("", response_model=PaginatedPatientResponse)
@limiter.limit("100/minute")
async def list_patients(
    request: Request,
    ctx: TenantContext,
    current_user: CurrentUser,
    _: AnyStaff,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    if current_user.role == Role.DOCTOR:
        base_where = [
            Patient.clinic_id == ctx.clinic_id,
            Patient.is_active.is_(True),
            Doctor.user_id == current_user.id,
        ]
        count_stmt = (
            select(func.count(Patient.id.distinct()))
            .join(Appointment, Appointment.patient_id == Patient.id)
            .join(Doctor, Doctor.id == Appointment.doctor_id)
            .where(*base_where)
        )
        data_stmt = (
            select(Patient)
            .join(Appointment, Appointment.patient_id == Patient.id)
            .join(Doctor, Doctor.id == Appointment.doctor_id)
            .where(*base_where)
            .distinct()
            .order_by(Patient.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    else:
        base_where = [
            Patient.clinic_id == ctx.clinic_id,
            Patient.is_active.is_(True),
        ]
        count_stmt = select(func.count()).select_from(Patient).where(*base_where)
        data_stmt = (
            select(Patient)
            .where(*base_where)
            .order_by(Patient.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

    total = (await ctx.db.execute(count_stmt)).scalar_one()
    items = (await ctx.db.execute(data_stmt)).scalars().all()

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/by-code/{code}", response_model=PatientResponse)
@limiter.limit("10/minute", key_func=get_ip_and_clinic)
async def get_patient_by_code(
    request: Request,
    code: str,
    ctx: TenantContext,
    _: AnyStaff,
):
    result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.medical_record_code == code.upper(),
            Patient.is_active.is_(True),
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    return patient


@router.get("/search", response_model=List[PatientResponse])
@limiter.limit("100/minute")
async def search_patients(
    request: Request,
    ctx: TenantContext,
    current_user: CurrentUser,
    _: AnyStaff,
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    pattern = f"%{q.lower()}%"

    if current_user.role == Role.DOCTOR:
        stmt = (
            select(Patient)
            .join(Appointment, Appointment.patient_id == Patient.id)
            .join(Doctor, Doctor.id == Appointment.doctor_id)
            .where(
                Patient.clinic_id == ctx.clinic_id,
                Patient.is_active.is_(True),
                Doctor.user_id == current_user.id,
                Patient.search_text.ilike(pattern),
            )
            .distinct()
            .limit(limit)
            .offset(offset)
        )
    else:
        stmt = (
            select(Patient)
            .where(
                Patient.clinic_id == ctx.clinic_id,
                Patient.is_active.is_(True),
                Patient.search_text.ilike(pattern),
            )
            .limit(limit)
            .offset(offset)
        )

    result = await ctx.db.execute(stmt)
    return result.scalars().all()


@router.get("/{patient_id}/timeline", response_model=List[Dict[str, Any]])
@limiter.limit("100/minute")
async def get_patient_timeline(
    request: Request,
    patient_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    """Chronological timeline: appointments, clinical notes, and PDF attachments."""
    if current_user.role == Role.DOCTOR:
        access = await ctx.db.execute(
            select(Patient)
            .join(Appointment, Appointment.patient_id == Patient.id)
            .join(Doctor, Doctor.id == Appointment.doctor_id)
            .where(
                Patient.clinic_id == ctx.clinic_id,
                Patient.id == patient_id,
                Patient.is_active.is_(True),
                Doctor.user_id == current_user.id,
            )
        )
        if access.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    else:
        check = await ctx.db.execute(
            select(Patient).where(
                Patient.clinic_id == ctx.clinic_id,
                Patient.id == patient_id,
                Patient.is_active.is_(True),
            )
        )
        if check.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    appts_result = await ctx.db.execute(
        select(Appointment)
        .options(joinedload(Appointment.doctor).joinedload(Doctor.user))
        .where(
            Appointment.clinic_id == ctx.clinic_id,
            Appointment.patient_id == patient_id,
        )
        .order_by(Appointment.starts_at.desc())
    )
    appointments = appts_result.scalars().unique().all()

    rec_q = select(MedicalRecord).where(
        MedicalRecord.clinic_id == ctx.clinic_id,
        MedicalRecord.patient_id == patient_id,
    )
    if current_user.role == Role.DOCTOR:
        dr_result = await ctx.db.execute(
            select(Doctor).where(
                Doctor.clinic_id == ctx.clinic_id,
                Doctor.user_id == current_user.id,
            )
        )
        doctor_profile = dr_result.scalar_one_or_none()
        if doctor_profile:
            rec_q = rec_q.where(MedicalRecord.doctor_id == doctor_profile.id)
        else:
            rec_q = rec_q.where(MedicalRecord.id.is_(None))

    records_result = await ctx.db.execute(rec_q.order_by(MedicalRecord.created_at.desc()))
    records = records_result.scalars().all()

    items: List[Dict[str, Any]] = []

    for appt in appointments:
        doctor_name = appt.doctor.user.full_name_enc if appt.doctor and appt.doctor.user else None
        items.append({
            "type": "appointment",
            "date": appt.starts_at.date().isoformat(),
            "doctor": doctor_name,
            "status": appt.status,
        })

    for record in records:
        if record.observations_enc:
            items.append({
                "type": "note",
                "date": record.created_at.date().isoformat(),
                "content": record.observations_enc,
            })
        if record.s3_pdf_key:
            items.append({
                "type": "pdf",
                "date": record.created_at.date().isoformat(),
                "url": f"/{record.s3_pdf_key}",
                "filename": Path(record.s3_pdf_key).name,
            })

    items.sort(key=lambda x: x["date"], reverse=True)
    return items


@router.get("/{patient_id}/can-book")
@limiter.limit("100/minute")
async def can_patient_book(
    request: Request,
    patient_id: uuid.UUID,
    ctx: TenantContext,
    _: AnyStaff,
):
    result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == patient_id,
            Patient.is_active.is_(True),
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    count = patient.no_show_count or 0
    can_book = count < 2
    reason = (
        "Paciente bloqueado por 2 o más inasistencias. Se requiere autorización del asistente."
        if not can_book else ""
    )
    return {"can_book": can_book, "reason": reason, "no_show_count": count}


@router.get("/{patient_id}", response_model=PatientResponse)
@limiter.limit("100/minute")
async def get_patient(
    request: Request,
    patient_id: uuid.UUID,
    ctx: TenantContext,
    _: AnyStaff,
):
    result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == patient_id,
            Patient.is_active.is_(True),
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    return patient


@router.put("/{patient_id}", response_model=PatientResponse)
@limiter.limit("100/minute")
async def update_patient(
    request: Request,
    patient_id: uuid.UUID,
    body: PatientUpdate,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == patient_id,
            Patient.is_active.is_(True),
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    updates = body.model_dump(exclude_unset=True)
    for field, enc_field in _ENC_FIELD_MAP.items():
        if field in updates:
            setattr(patient, enc_field, updates[field])

    if "full_name" in updates:
        patient.full_name_search_hash = make_search_hash(
            updates["full_name"].lower().strip())
    if "phone" in updates:
        patient.phone_search_hash = (
            make_search_hash(
                _digits(updates["phone"])) if updates["phone"] else None
        )

    if any(k in updates for k in ("full_name", "email", "phone")):
        patient.search_text = _build_search_text(
            patient.full_name_enc,
            patient.email_enc,
            patient.phone_enc,
        )

    patient.key_version = _key_version()
    changed = list(updates.keys())
    masked = [_ENC_FIELD_MAP[f] for f in changed if f in _ENC_FIELD_MAP]
    plain_changes = {f: updates[f] for f in changed if f not in _ENC_FIELD_MAP}
    await log_audit(
        ctx.db,
        event_type=EventType.PATIENT_UPDATED,
        entity_type="Patient",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=patient_id,
        source=request.url.path,
        data={
            "actor_role": current_user.role,
            "changed_fields": changed,
            "masked_fields": masked,
            "after": plain_changes,
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()
    await ctx.db.refresh(patient)
    return patient


@router.post("/{patient_id}/regenerate-code", response_model=PatientResponse)
@limiter.limit("20/minute")
async def regenerate_patient_code(
    request: Request,
    patient_id: uuid.UUID,
    ctx: TenantContext,
    _: OwnerOnly,
):
    result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == patient_id,
            Patient.is_active.is_(True),
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    patient.medical_record_code = await _generate_patient_code(ctx.db, ctx.clinic_id)
    await ctx.db.commit()
    await ctx.db.refresh(patient)
    return patient


@router.post("/{source_id}/merge/{target_id}")
@limiter.limit("20/minute")
async def merge_patients(
    request: Request,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    ctx: TenantContext,
    _: OwnerOnly,
):
    if source_id == target_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Source and target must be different patients.",
        )

    source_result = await ctx.db.execute(
        select(Patient).where(Patient.clinic_id ==
                              ctx.clinic_id, Patient.id == source_id)
    )
    source = source_result.scalar_one_or_none()
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Source patient not found.")

    target_result = await ctx.db.execute(
        select(Patient).where(Patient.clinic_id ==
                              ctx.clinic_id, Patient.id == target_id)
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="Target patient not found.")

    if not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Target patient is inactive.",
        )

    # Leer nombres antes del commit (esto arregla el bug)
    source_name = source.full_name_enc
    target_name = target.full_name_enc

    await ctx.db.execute(
        update(Appointment)
        .where(Appointment.clinic_id == ctx.clinic_id, Appointment.patient_id == source_id)
        .values(patient_id=target_id)
    )

    await ctx.db.execute(
        update(MedicalRecord)
        .where(MedicalRecord.clinic_id == ctx.clinic_id, MedicalRecord.patient_id == source_id)
        .values(patient_id=target_id)
    )

    source.is_active = False
    await ctx.db.commit()

    return {
        "message": f"Paciente {source_name} fusionado en {target_name}",
        "target_id": str(target_id),
    }


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("100/minute")
async def delete_patient(
    request: Request,
    patient_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == patient_id,
            Patient.is_active.is_(True),
        )
    )
    patient = result.scalar_one_or_none()
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    patient.is_active = False
    await log_audit(
        ctx.db,
        event_type=EventType.PATIENT_DELETED,
        entity_type="Patient",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=patient_id,
        source=request.url.path,
        data={"actor_role": current_user.role, "soft_delete": True},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()
