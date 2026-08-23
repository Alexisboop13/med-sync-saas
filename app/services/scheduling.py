"""
app/services/scheduling.py
──────────────────────────────────────────────────────────────────────────────
Shared doctor-schedule helpers.

Used by both the public self-booking flow (app/api/routes/booking.py) and the
staff-facing appointments API (app/api/routes/appointments.py) so working-hours
validation stays identical regardless of who is creating/moving the
appointment.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from app.models.doctor import Doctor

DAY_ABBR = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_NAMES_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def validate_working_hours(doctor: Doctor, starts_at: datetime, ends_at: datetime) -> None:
    """Raise 422 if the requested slot falls outside the doctor's working_hours."""
    working_hours = doctor.working_hours or {}
    day_key = DAY_ABBR[starts_at.weekday()]
    schedule = working_hours.get(day_key, [])

    if not schedule:
        raise HTTPException(
            status_code=422,
            detail=(
                f"El doctor no atiende los {_DAY_NAMES_ES[starts_at.weekday()]}. "
                "Por favor selecciona otro día."
            ),
        )

    slot_start_minutes = starts_at.hour * 60 + starts_at.minute
    slot_end_minutes = ends_at.hour * 60 + ends_at.minute

    for block in schedule:
        try:
            bsh, bsm = map(int, block["start"].split(":"))
            beh, bem = map(int, block["end"].split(":"))
            block_start = bsh * 60 + bsm
            block_end = beh * 60 + bem
            if block_start <= slot_start_minutes and slot_end_minutes <= block_end:
                return
        except (KeyError, ValueError):
            continue

    schedule_str = ", ".join(
        f"{b.get('start', '?')}–{b.get('end', '?')}"
        for b in schedule
        if isinstance(b, dict)
    )
    raise HTTPException(
        status_code=422,
        detail=(
            f"El horario solicitado está fuera del horario del doctor. "
            f"Horario disponible ese día: {schedule_str}."
        ),
    )
