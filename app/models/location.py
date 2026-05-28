from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from sqlalchemy import String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import TenantBase

if TYPE_CHECKING:
    from app.models.appointment import Appointment


class Location(TenantBase):
    """Physical clinic branch or location. Inherits clinic_id, id, timestamps from TenantBase."""

    __tablename__ = "locations"

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name, e.g. 'Jardines de Morelos'.",
    )

    address: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    google_maps_url: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="Google Maps share link for patient-facing booking pages.",
    )

    rooms: Mapped[Dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
        comment=(
            "Consultory/room structure. Schema: "
            "{consultorioN: {units: [...], colors: {unitName: '#hex'}}}."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    appointments: Mapped[List["Appointment"]] = relationship(
        "Appointment",
        back_populates="location",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Location id={self.id} name={self.name!r} clinic={self.clinic_id}>"
