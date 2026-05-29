"""
app/agent/background.py
──────────────────────────────────────────────────────────────────────────────
APScheduler integration — registra el agente para correr cada 10 minutos.

IMPORTANTE — Render Free Tier:
  El servicio duerme tras 15 min de inactividad. El scheduler vive en proceso,
  así que también se detiene. Soluciones complementarias:

  1. UptimeRobot (gratis): monitorea /health cada 5 minutos → mantiene vivo.
     https://uptimerobot.com — crea un monitor HTTP para tu URL de Render.

  2. Render Cron Job (gratis): crea un cron job separado que llame a
     POST /internal/agent/run cada hora. Ve a Render → New → Cron Job.
     Comando: curl -X POST https://tu-app.onrender.com/internal/agent/run \
               -H "Authorization: Bearer $AGENT_SECRET_KEY"

  Con UptimeRobot + APScheduler, los recordatorios funcionan si el servicio
  está despierto. Sin UptimeRobot, el scheduler puede perderse citas.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _run_agent_job() -> None:
    """Job que corre cada 10 minutos."""
    from app.agent.scheduler import run_all_tasks  # noqa: PLC0415
    from app.db.session import AsyncSessionLocal  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        try:
            result = await run_all_tasks(db)
            if result.reminders_sent or result.reminders_failed or result.noshows_marked:
                log.info(
                    "Agente: %d recordatorios enviados, %d fallidos, %d no-shows.",
                    result.reminders_sent,
                    result.reminders_failed,
                    result.noshows_marked,
                )
        except Exception:
            log.error("Error en job del agente", exc_info=True)


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_agent_job,
        trigger=IntervalTrigger(minutes=10),
        id="agent_reminder_job",
        name="Recordatorios automáticos",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    log.info("Scheduler del agente iniciado (cada 10 minutos).")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("Scheduler del agente detenido.")


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
