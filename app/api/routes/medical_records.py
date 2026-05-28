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
from app.core.limiter import limiter
from app.core.crypto import _load_keyring
from app.models.doctor import Doctor
from app.models.medical_record import MedicalRecord
from app.models.patient import Patient
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

    await ctx.db.delete(record)
    await ctx.db.commit()


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

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S%f")
    upload_dir = Path("uploads") / "medical-records" / str(ctx.clinic_id) / str(record_id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / f"{timestamp}.pdf"
    dest.write_bytes(content)

    record.s3_pdf_key = dest.as_posix()
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

    base = Path("uploads").resolve()
    file_path = Path(record.s3_pdf_key).resolve()
    if not str(file_path).startswith(str(base) + "/"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="PDF file not found on disk.",
        )

    disposition = "inline" if inline else "attachment"
    filename = f"record_{record_id}.pdf"
    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{filename}"'},
    )
