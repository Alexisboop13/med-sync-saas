from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.appointment import AppointmentStatus

_STATUS_COLORS: dict[str, str] = {
    "scheduled":           "#3b82f6",
    "confirmed":           "#10b981",
    "completed":           "#6b7280",
    "canceled":            "#ef4444",
    "canceled_by_patient": "#ef4444",
    "no_show":             "#f59e0b",
    "in_progress":         "#8b5cf6",
    "pending_reschedule":  "#f59e0b",
}


class AgendaPatientInfo(BaseModel):
    id: uuid.UUID
    full_name: str = Field(alias="full_name_enc")
    phone: Optional[str] = Field(None, alias="phone_enc")
    email: Optional[str] = Field(None, alias="email_enc")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class AgendaDoctorInfo(BaseModel):
    id: uuid.UUID
    specialty: str
    full_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def _extract_user_name(cls, v: object) -> object:
        if hasattr(v, "user") and v.user is not None:
            return {"id": v.id, "specialty": v.specialty, "full_name": v.user.full_name_enc}
        return v


class AgendaResponse(BaseModel):
    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: str
    reason: Optional[str] = None
    patient: AgendaPatientInfo
    doctor: AgendaDoctorInfo

    model_config = ConfigDict(from_attributes=True)

_VALID_STATUSES = {s.value for s in AppointmentStatus}


class AppointmentCreate(BaseModel):
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    location_id: Optional[uuid.UUID] = None
    starts_at: datetime
    ends_at: datetime
    reason: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def check_time_range(self) -> "AppointmentCreate":
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at.")
        return self


class AppointmentUpdate(BaseModel):
    location_id: Optional[uuid.UUID] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    status: Optional[str] = None
    reason: Optional[str] = None
    notes: Optional[str] = None

    @model_validator(mode="after")
    def validate_fields(self) -> "AppointmentUpdate":
        if self.starts_at is not None and self.ends_at is not None:
            if self.starts_at >= self.ends_at:
                raise ValueError("starts_at must be before ends_at.")
        if self.status is not None and self.status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status. Valid values: {sorted(_VALID_STATUSES)}")
        return self


class AppointmentResponse(BaseModel):
    id: uuid.UUID
    clinic_id: uuid.UUID
    doctor_id: uuid.UUID
    patient_id: uuid.UUID
    location_id: Optional[uuid.UUID]
    created_by_id: Optional[uuid.UUID]
    starts_at: datetime
    ends_at: datetime
    status: str
    reason: Optional[str]
    notes: Optional[str] = Field(None, alias="notes_enc")
    key_version: int
    magic_token: Optional[str]
    magic_token_expires_at: Optional[datetime]
    proposed_starts_at: Optional[datetime] = None
    proposed_ends_at: Optional[datetime] = None
    reschedule_token: Optional[str] = None
    reschedule_token_expires_at: Optional[datetime] = None
    patient_confirmed_at: Optional[datetime] = None
    patient_confirmation_channel: Optional[str] = None
    was_no_show: bool = False
    created_at: datetime
    updated_at: datetime
    doctor_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    @computed_field
    @property
    def color(self) -> str:
        return _STATUS_COLORS.get(self.status, "#6b7280")


class PublicAppointmentResponse(BaseModel):
    id: uuid.UUID
    doctor_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: str
    reason: Optional[str] = None
    patient_name: str
    doctor_name: str
    formatted_date: str = ""
    formatted_time: str = ""
    patient_confirmed_at: Optional[datetime] = None
    can_cancel: bool = True


class PublicAppointmentAction(BaseModel):
    action: Literal["confirm", "cancel", "reschedule"]
    new_starts_at: Optional[datetime] = None
    new_ends_at: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_reschedule(self) -> "PublicAppointmentAction":
        if self.action == "reschedule":
            if self.new_starts_at is None or self.new_ends_at is None:
                raise ValueError("new_starts_at y new_ends_at son requeridos para reprogramar.")
            if self.new_starts_at >= self.new_ends_at:
                raise ValueError("new_starts_at debe ser anterior a new_ends_at.")
        return self


class AppointmentStatusPatch(BaseModel):
    status: Literal["scheduled", "completed", "canceled"]


class SlotItem(BaseModel):
    starts_at: datetime
    ends_at: datetime


class SlotsResponse(BaseModel):
    doctor_id: uuid.UUID
    slots: List[SlotItem]


class AppointmentWithRescheduleResponse(AppointmentResponse):
    reschedule_request_id: Optional[uuid.UUID] = None
    reschedule_request_note: Optional[str] = None
    reschedule_requested_at: Optional[datetime] = None


class PaginatedAppointmentResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: List[AppointmentWithRescheduleResponse]


class AppointmentNoteAdd(BaseModel):
    note: str = Field(..., min_length=1, max_length=5000)


class AppointmentNoteSet(BaseModel):
    note: str = Field(..., min_length=0, max_length=5000)


class AppointmentNoteCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class AppointmentNoteResponse(BaseModel):
    id: uuid.UUID
    appointment_id: uuid.UUID
    clinic_id: uuid.UUID
    content: str
    created_by_id: Optional[uuid.UUID] = None
    author_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProposeRescheduleBody(BaseModel):
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def check_time_range(self) -> "ProposeRescheduleBody":
        if self.starts_at >= self.ends_at:
            raise ValueError("starts_at must be before ends_at.")
        return self
