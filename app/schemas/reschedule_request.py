from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.reschedule_request import RescheduleRequestStatus


class RescheduleRequestCreate(BaseModel):
    patient_note: Optional[str] = None


class RescheduleRequestPublicBody(BaseModel):
    note: Optional[str] = None


class RescheduleRequestResolve(BaseModel):
    status: RescheduleRequestStatus

    model_config = ConfigDict(use_enum_values=True)


class RescheduleRequestResponse(BaseModel):
    id: uuid.UUID
    clinic_id: uuid.UUID
    appointment_id: uuid.UUID
    patient_note: Optional[str]
    status: str
    requested_at: datetime
    resolved_at: Optional[datetime]
    resolved_by_id: Optional[uuid.UUID]

    model_config = ConfigDict(from_attributes=True)
