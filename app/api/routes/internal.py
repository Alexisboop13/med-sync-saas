from fastapi import APIRouter, Request

from app.api.deps import OwnerOnly, TenantContext
from app.core.limiter import limiter
from app.services.cleanup_service import expire_pending_reschedules

router = APIRouter(prefix="/internal", tags=["Internal"])


@router.post("/cleanup/pending-reschedules")
@limiter.limit("10/minute")
async def cleanup_pending_reschedules(
    request: Request,
    ctx: TenantContext,
    _: OwnerOnly,
):
    cleaned = await expire_pending_reschedules(ctx.db, clinic_id=ctx.clinic_id)
    return {"cleaned": cleaned, "message": f"{cleaned} cita(s) reseteadas a 'scheduled'."}
