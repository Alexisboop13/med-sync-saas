from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TimeSlot(BaseModel):
    start: str = Field(..., pattern=r"^\d{2}:\d{2}$", examples=["09:00"])
    end: str = Field(..., pattern=r"^\d{2}:\d{2}$", examples=["13:00"])

    @model_validator(mode="after")
    def end_after_start(self) -> "TimeSlot":
        if self.end <= self.start:
            raise ValueError(f"end '{self.end}' must be after start '{self.start}'")
        return self


_VALID_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

_DAY_FULL_TO_ABBR: Dict[str, str] = {
    "monday": "mon", "tuesday": "tue", "wednesday": "wed",
    "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
    "mon": "mon", "tue": "tue", "wed": "wed",
    "thu": "thu", "fri": "fri", "sat": "sat", "sun": "sun",
}


def _parse_time_slot(slot: Any) -> Dict[str, str]:
    """Accept 'HH:MM-HH:MM' string or {'start': ..., 'end': ...} dict."""
    if isinstance(slot, str):
        match = re.match(r"^(\d{2}:\d{2})-(\d{2}:\d{2})$", slot)
        if not match:
            raise ValueError(f"Invalid slot format: {slot!r}. Expected 'HH:MM-HH:MM'.")
        return {"start": match.group(1), "end": match.group(2)}
    if isinstance(slot, dict):
        return slot
    raise ValueError(f"Slot must be 'HH:MM-HH:MM' string or dict, got {type(slot).__name__}.")


def _validate_working_hours(v: Any) -> Dict[str, List[Dict[str, str]]]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError("working_hours must be a dict")
    invalid = set(v.keys()) - _VALID_DAYS
    if invalid:
        raise ValueError(f"Invalid day keys: {invalid}. Must be one of {_VALID_DAYS}")
    for day, slots in v.items():
        if not isinstance(slots, list):
            raise ValueError(f"working_hours['{day}'] must be a list")
        for slot in slots:
            TimeSlot(**slot)
    return v


def _normalize_working_hours_input(v: Any) -> Optional[Dict[str, List[Dict[str, str]]]]:
    """Accepts monday/mon keys and 'HH:MM-HH:MM' strings or dict slots."""
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ValueError("working_hours must be a dict")
    normalized: Dict[str, List[Dict[str, str]]] = {}
    for day, slots in v.items():
        abbr = _DAY_FULL_TO_ABBR.get(str(day).lower())
        if abbr is None:
            raise ValueError(
                f"Invalid day key: {day!r}. Use 'mon'–'sun' or full names like 'monday'."
            )
        if not isinstance(slots, list):
            raise ValueError(f"working_hours['{day}'] must be a list of time slots")
        parsed: List[Dict[str, str]] = []
        for slot in slots:
            s = _parse_time_slot(slot)
            TimeSlot(**s)
            parsed.append(s)
        normalized[abbr] = parsed
    return normalized


class DoctorCreate(BaseModel):
    user_id: uuid.UUID
    title: str = Field(default="Dr.", max_length=10)
    specialty: str = Field(default="General Practice", max_length=120)
    bio: Optional[str] = Field(None, max_length=1000)
    avatar_s3_key: Optional[str] = Field(None, max_length=500)
    working_hours: Optional[Dict[str, List[Dict[str, str]]]] = Field(default_factory=dict)
    appointment_duration_minutes: int = Field(default=30, ge=5, le=480)
    is_accepting_patients: bool = True

    @field_validator("specialty")
    @classmethod
    def specialty_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("specialty cannot be empty")
        return v.strip()

    @field_validator("working_hours", mode="before")
    @classmethod
    def validate_working_hours(cls, v: Any) -> Any:
        return _validate_working_hours(v)


class DoctorUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=10)
    specialty: Optional[str] = Field(None, max_length=120)
    bio: Optional[str] = Field(None, max_length=1000)
    avatar_s3_key: Optional[str] = Field(None, max_length=500)
    working_hours: Optional[Dict[str, List[Dict[str, str]]]] = None
    appointment_duration_minutes: Optional[int] = Field(None, ge=5, le=480)
    is_accepting_patients: Optional[bool] = None

    @field_validator("specialty")
    @classmethod
    def specialty_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.strip():
            raise ValueError("specialty cannot be empty")
        return v.strip() if v else v

    @field_validator("working_hours", mode="before")
    @classmethod
    def validate_working_hours(cls, v: Any) -> Any:
        if v is None:
            return v
        return _validate_working_hours(v)


class DoctorResponse(BaseModel):
    id: uuid.UUID
    clinic_id: uuid.UUID
    user_id: uuid.UUID
    title: str = "Dr."
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    specialty: str
    bio: Optional[str]
    avatar_s3_key: Optional[str]
    working_hours: Dict[str, Any]
    appointment_duration_minutes: int
    is_accepting_patients: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkingHoursUpdateRequest(BaseModel):
    """Body for PUT /doctors/{id}/working-hours."""
    working_hours: Optional[Dict[str, Any]] = None
    is_available: Optional[bool] = None

    @field_validator("working_hours", mode="before")
    @classmethod
    def normalize_hours(cls, v: Any) -> Any:
        return _normalize_working_hours_input(v)


class DoctorAvailableResponse(BaseModel):
    """Doctor response for GET /doctors/available — includes time blocks for the queried day."""
    id: uuid.UUID
    clinic_id: uuid.UUID
    user_id: uuid.UUID
    full_name: Optional[str] = None
    specialty: str
    bio: Optional[str]
    avatar_s3_key: Optional[str]
    appointment_duration_minutes: int
    is_accepting_patients: bool
    day_slots: List[Dict[str, str]]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
