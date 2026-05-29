from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.api.deps import AnyStaff, OwnerOnly, TenantContext
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


# ── Agent endpoints ───────────────────────────────────────────────────────────

def _verify_agent_key(authorization: str | None = Header(default=None)) -> None:
    """Valida Bearer token para el endpoint de trigger externo del agente."""
    secret = os.environ.get("AGENT_SECRET_KEY", "")
    if not secret:
        raise HTTPException(status_code=503, detail="AGENT_SECRET_KEY no configurada.")
    expected = f"Bearer {secret}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Token de agente inválido.")


@router.post("/agent/run")
@limiter.limit("6/minute")
async def run_agent(
    request: Request,
    _auth: None = Depends(_verify_agent_key),
):
    """
    Trigger externo del agente — llámalo desde un Render Cron Job o UptimeRobot.
    Protegido con Bearer token (AGENT_SECRET_KEY en env).

    Ejemplo desde Render Cron Job:
      curl -X POST https://tu-app.onrender.com/internal/agent/run \\
           -H "Authorization: Bearer $AGENT_SECRET_KEY"
    """
    from app.agent.scheduler import run_all_tasks  # noqa: PLC0415
    from app.db.session import AsyncSessionLocal  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        result = await run_all_tasks(db)

    return {
        "reminders_sent": result.reminders_sent,
        "reminders_failed": result.reminders_failed,
        "noshows_marked": result.noshows_marked,
        "errors": result.errors,
        "ran_at": result.ran_at.isoformat(),
    }


@router.get("/agent/status")
@limiter.limit("30/minute")
async def agent_status(
    request: Request,
    ctx: TenantContext,
    _: AnyStaff,
):
    """
    Dashboard del agente: próximas citas + historial de notificaciones.
    Visible para cualquier staff (owner, doctor, assistant).
    """
    from app.agent.scheduler import get_agent_status  # noqa: PLC0415

    data = await get_agent_status(ctx.db)
    return data
