from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DoctorOrAbove, TenantContext, ClinicContext, Role
from app.core import s3
from app.core.config import settings
from app.core.limiter import limiter
from app.core.crypto import _load_keyring
from app.models.audit_log import EventType
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
from app.services.audit import log_audit
from app.schemas.medical_record import (
    MedicalRecordCreate,
    MedicalRecordResponse,
    MedicalRecordUpdate,
    PaginatedMedicalRecordResponse,
)

_MAX_PDF_BYTES = 10 * 1024 * 1024  # 10 MB

router = APIRouter(prefix="/medical-records", tags=["Medical Records"])

_ENC_FIELD_MAP = {
    "diagnosis": "diagnosis_enc",
    "treatment": "treatment_enc",
    "prescription": "prescription_enc",
    "observations": "observations_enc",
    "vitals": "vitals_enc",
}


def _key_version() -> int:
    ring = _load_keyring()
    return max(ring)


async def _get_doctor_for_user(ctx: ClinicContext, user_id: uuid.UUID) -> Optional[Doctor]:
    result = await ctx.db.execute(
        select(Doctor).where(
            Doctor.clinic_id == ctx.clinic_id,
            Doctor.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _assert_can_access(ctx: ClinicContext, record: MedicalRecord, current_user) -> None:
    if current_user.role == Role.OWNER:
        return
    doctor = await _get_doctor_for_user(ctx, current_user.id)
    if doctor is None or record.doctor_id != doctor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access records where you are the assigned doctor.",
        )


@router.post("", response_model=MedicalRecordResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("100/minute")
async def create_medical_record(
    request: Request,
    body: MedicalRecordCreate,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    patient_result = await ctx.db.execute(
        select(Patient).where(
            Patient.clinic_id == ctx.clinic_id,
            Patient.id == body.patient_id,
            Patient.is_active.is_(True),
        )
    )
    if patient_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")

    doctor_result = await ctx.db.execute(
        select(Doctor).where(
            Doctor.clinic_id == ctx.clinic_id,
            Doctor.id == body.doctor_id,
        )
    )
    if doctor_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Doctor not found.")

    record = MedicalRecord(
        clinic_id=ctx.clinic_id,
        patient_id=body.patient_id,
        doctor_id=body.doctor_id,
        appointment_id=body.appointment_id,
        diagnosis_enc=body.diagnosis,
        treatment_enc=body.treatment,
        prescription_enc=body.prescription,
        observations_enc=body.observations,
        vitals_enc=body.vitals,
        s3_pdf_key=body.s3_pdf_key,
        key_version=_key_version(),
    )
    ctx.db.add(record)
    await ctx.db.flush()
    await log_audit(
        ctx.db,
        event_type=EventType.RECORD_CREATED,
        entity_type="MedicalRecord",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=record.id,
        source=request.url.path,
        data={
            "actor_role": current_user.role,
            "patient_id": str(body.patient_id),
            "doctor_id": str(body.doctor_id),
            "masked_fields": list(_ENC_FIELD_MAP.values()),
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()
    await ctx.db.refresh(record)
    return record


@router.get("", response_model=PaginatedMedicalRecordResponse)
@limiter.limit("100/minute")
async def list_medical_records(
    request: Request,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    patient_id: Optional[uuid.UUID] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    base_filters = [MedicalRecord.clinic_id == ctx.clinic_id]

    if current_user.role != Role.OWNER:
        doctor = await _get_doctor_for_user(ctx, current_user.id)
        if doctor is None:
            return {"total": 0, "limit": limit, "offset": offset, "items": []}
        base_filters.append(MedicalRecord.doctor_id == doctor.id)

    if patient_id is not None:
        base_filters.append(MedicalRecord.patient_id == patient_id)

    total = (
        await ctx.db.execute(select(func.count()).select_from(MedicalRecord).where(*base_filters))
    ).scalar_one()

    items = (
        await ctx.db.execute(
            select(MedicalRecord)
            .where(*base_filters)
            .order_by(MedicalRecord.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/{record_id}", response_model=MedicalRecordResponse)
@limiter.limit("100/minute")
async def get_medical_record(
    request: Request,
    record_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(MedicalRecord).where(
            MedicalRecord.clinic_id == ctx.clinic_id,
            MedicalRecord.id == record_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medical record not found.")

    await _assert_can_access(ctx, record, current_user)
    await log_audit(
        ctx.db,
        event_type=EventType.RECORD_VIEWED,
        entity_type="MedicalRecord",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=record_id,
        source=request.url.path,
        data={"actor_role": current_user.role},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        commit=True,
    )
    return record


@router.put("/{record_id}", response_model=MedicalRecordResponse)
@limiter.limit("100/minute")
async def update_medical_record(
    request: Request,
    record_id: uuid.UUID,
    body: MedicalRecordUpdate,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(MedicalRecord).where(
            MedicalRecord.clinic_id == ctx.clinic_id,
            MedicalRecord.id == record_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medical record not found.")

    await _assert_can_access(ctx, record, current_user)

    if record.is_signed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Signed records are immutable.",
        )

    updates = body.model_dump(exclude_unset=True)

    if updates.get("is_signed") is True:
        record.is_signed = True
        record.signed_at = datetime.now(timezone.utc)
        record.signed_by_id = current_user.id
        updates.pop("is_signed")

    for field, enc_field in _ENC_FIELD_MAP.items():
        if field in updates:
            setattr(record, enc_field, updates[field])

    if "s3_pdf_key" in updates:
        record.s3_pdf_key = updates["s3_pdf_key"]

    record.key_version = _key_version()
    was_signed = record.is_signed and "is_signed" in body.model_dump(exclude_unset=True)
    changed_fields = list(updates.keys())
    masked = [_ENC_FIELD_MAP[f] for f in changed_fields if f in _ENC_FIELD_MAP]
    _event = EventType.RECORD_SIGNED if was_signed else EventType.RECORD_UPDATED
    await log_audit(
        ctx.db,
        event_type=_event,
        entity_type="MedicalRecord",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=record_id,
        source=request.url.path,
        data={
            "actor_role": current_user.role,
            "changed_fields": changed_fields,
            "masked_fields": masked,
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()
    await ctx.db.refresh(record)
    return record


@router.delete("/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("100/minute")
async def delete_medical_record(
    request: Request,
    record_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
):
    result = await ctx.db.execute(
        select(MedicalRecord).where(
            MedicalRecord.clinic_id == ctx.clinic_id,
            MedicalRecord.id == record_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medical record not found.")

    await _assert_can_access(ctx, record, current_user)

    if record.is_signed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Signed records cannot be deleted.",
        )

    key = record.s3_pdf_key
    await log_audit(
        ctx.db,
        event_type="medical_record.deleted",
        entity_type="MedicalRecord",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=record_id,
        source=request.url.path,
        data={"actor_role": current_user.role, "had_pdf": bool(key)},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.delete(record)
    await ctx.db.commit()
    if key and not key.startswith("uploads/"):
        await s3.delete_file(key)


async def _fetch_and_authorize(
    record_id: uuid.UUID, ctx: ClinicContext, current_user
) -> MedicalRecord:
    result = await ctx.db.execute(
        select(MedicalRecord).where(
            MedicalRecord.clinic_id == ctx.clinic_id,
            MedicalRecord.id == record_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Medical record not found.")
    await _assert_can_access(ctx, record, current_user)
    return record


@router.post("/{record_id}/upload", response_model=MedicalRecordResponse)
@limiter.limit("10/minute")
async def upload_pdf(
    request: Request,
    record_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    file: UploadFile = File(...),
):
    record = await _fetch_and_authorize(record_id, ctx, current_user)

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

    key = s3.build_key(str(ctx.clinic_id), str(record_id))
    await s3.upload_file(content, key)

    record.s3_pdf_key = key
    await log_audit(
        ctx.db,
        event_type=EventType.RECORD_PDF_UPLOADED,
        entity_type="MedicalRecord",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=record_id,
        source=request.url.path,
        data={"actor_role": current_user.role, "s3_key": key},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()
    await ctx.db.refresh(record)
    return record


@router.get("/{record_id}/download")
@limiter.limit("100/minute")
async def download_pdf(
    request: Request,
    record_id: uuid.UUID,
    ctx: TenantContext,
    current_user: DoctorOrAbove,
    inline: bool = Query(True, description="True para ver en navegador, False para descargar"),
):
    record = await _fetch_and_authorize(record_id, ctx, current_user)

    if not record.s3_pdf_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This record has no PDF attached.",
        )

    key = record.s3_pdf_key
    filename = f"record_{record_id}.pdf"

    await log_audit(
        ctx.db,
        event_type=EventType.RECORD_PDF_DOWNLOADED,
        entity_type="MedicalRecord",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=record_id,
        source=request.url.path,
        data={"actor_role": current_user.role},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        commit=True,
    )

    # Backward compat: records uploaded before S3 migration have a local path.
    # Run scripts/migrate_pdfs_to_s3.py to convert these and remove this branch.
    if key.startswith("uploads/"):
        base = Path("uploads").resolve()
        file_path = Path(key).resolve()
        if not str(file_path).startswith(str(base) + "/"):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        if not file_path.exists():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="PDF file not found on disk.",
            )
        disposition = "inline" if inline else "attachment"
        return FileResponse(
            path=str(file_path),
            media_type="application/pdf",
            headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
        )

    url = await s3.presigned_download_url(key, filename=filename, inline=inline)
    return {"url": url, "expires_in": settings.S3_PRESIGNED_URL_TTL}
