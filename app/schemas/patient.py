from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class PatientCreate(BaseModel):
    full_name: str
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    address: Optional[str] = None
    blood_type: Optional[str] = None
    allergies: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None

    @field_validator("full_name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("full_name cannot be empty.")
        return v.strip()


class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    address: Optional[str] = None
    blood_type: Optional[str] = None
    allergies: Optional[dict[str, Any]] = None
    notes: Optional[str] = None
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None


class PatientResponse(BaseModel):
    id: uuid.UUID
    clinic_id: uuid.UUID
    medical_record_code: str
    full_name: str = Field(alias="full_name_enc")
    phone: Optional[str] = Field(None, alias="phone_enc")
    email: Optional[str] = Field(None, alias="email_enc")
    date_of_birth: Optional[str] = Field(None, alias="date_of_birth_enc")
    gender: Optional[str] = Field(None, alias="gender_enc")
    address: Optional[str] = Field(None, alias="address_enc")
    blood_type: Optional[str] = Field(None, alias="blood_type_enc")
    allergies: Optional[dict[str, Any]] = Field(None, alias="allergies_enc")
    notes: Optional[str] = Field(None, alias="notes_enc")
    emergency_contact_name: Optional[str] = Field(None, alias="emergency_contact_name_enc")
    emergency_contact_phone: Optional[str] = Field(None, alias="emergency_contact_phone_enc")
    no_show_count: int = 0
    last_no_show_at: Optional[datetime] = None
    is_active: bool
    key_version: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class PaginatedPatientResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[PatientResponse]
