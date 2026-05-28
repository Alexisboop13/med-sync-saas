from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment, AppointmentStatus


async def expire_pending_reschedules(
    db: AsyncSession,
    clinic_id: UUID | None = None,
) -> int:
    """
    Reset appointments stuck in PENDING_RESCHEDULE whose token has expired.
    Returns the number of rows updated.
    """
    now = datetime.now(timezone.utc)
    conditions = [
        Appointment.status == AppointmentStatus.PENDING_RESCHEDULE,
        Appointment.reschedule_token_expires_at < now,
    ]
    if clinic_id is not None:
        conditions.append(Appointment.clinic_id == clinic_id)

    result = await db.execute(
        update(Appointment)
        .where(*conditions)
        .values(
            status=AppointmentStatus.SCHEDULED,
            proposed_starts_at=None,
            proposed_ends_at=None,
            reschedule_token=None,
            reschedule_token_expires_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    return result.rowcount
