from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MedicalRecordCreate(BaseModel):
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    appointment_id: Optional[uuid.UUID] = None
    diagnosis: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[List[dict[str, Any]]] = None
    observations: Optional[str] = None
    vitals: Optional[dict[str, Any]] = None
    s3_pdf_key: Optional[str] = None


class MedicalRecordUpdate(BaseModel):
    diagnosis: Optional[str] = None
    treatment: Optional[str] = None
    prescription: Optional[List[dict[str, Any]]] = None
    observations: Optional[str] = None
    vitals: Optional[dict[str, Any]] = None
    s3_pdf_key: Optional[str] = None
    is_signed: Optional[bool] = None

    @field_validator("is_signed")
    @classmethod
    def cannot_unsign(cls, v: Optional[bool]) -> Optional[bool]:
        if v is False:
            raise ValueError("is_signed can only be set to True (records cannot be unsigned).")
        return v


class MedicalRecordResponse(BaseModel):
    id: uuid.UUID
    clinic_id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    appointment_id: Optional[uuid.UUID]
    diagnosis: Optional[str] = Field(None, alias="diagnosis_enc")
    treatment: Optional[str] = Field(None, alias="treatment_enc")
    prescription: Optional[List[dict[str, Any]]] = Field(None, alias="prescription_enc")
    observations: Optional[str] = Field(None, alias="observations_enc")
    vitals: Optional[dict[str, Any]] = Field(None, alias="vitals_enc")
    s3_pdf_key: Optional[str]
    is_signed: bool
    signed_at: Optional[datetime]
    signed_by_id: Optional[uuid.UUID]
    key_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PaginatedMedicalRecordResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[MedicalRecordResponse]
