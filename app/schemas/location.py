from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class LocationCreate(BaseModel):
    name: str = Field(..., max_length=100)
    address: str
    google_maps_url: Optional[str] = Field(None, max_length=500)
    rooms: Dict[str, Any] = Field(default_factory=dict)


class LocationUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    address: Optional[str] = None
    google_maps_url: Optional[str] = Field(None, max_length=500)
    rooms: Optional[Dict[str, Any]] = None


class LocationResponse(BaseModel):
    id: uuid.UUID
    clinic_id: uuid.UUID
    name: str
    address: str
    google_maps_url: Optional[str]
    rooms: Dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
