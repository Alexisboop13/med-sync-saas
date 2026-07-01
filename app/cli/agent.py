"""
app/cli/agent.py
──────────────────────────────────────────────────────────────────────────────
Agente de citas de Med-Sync — versión terminal.

Reutiliza exactamente las mismas funciones que usaba el scheduler en
proceso (retirado — ver migrations Paso 4) y el trigger HTTP
(app/api/routes/internal.py): `run_all_tasks()` y `get_agent_status()`
de app/agent/scheduler.py. Este CLI habla directo con la DB — sin HTTP,
sin AGENT_SECRET_KEY.

Como no hay JWT en terminal, el "usuario actual" se resuelve igual que el
login real (auth.py): por email_search_hash, desambiguando por --clinic-id
si ese email existe en más de una clínica. El clinic_id resuelto se usa
para acotar `status` a esa clínica — nunca se acepta clinic_id sin
verificar antes que el email pertenece a esa clínica.

`run` es una operación global (procesa recordatorios de TODAS las
clínicas, igual que el cron en producción) — no necesita atarse a la
cuenta de un humano. --email es opcional ahí y sirve solo para trazar
"quién disparó esta corrida" en corridas manuales; el cron de producción
debe invocarlo sin --email.

Uso:
    python -m app.cli.agent status --email owner@clinic.com
    python -m app.cli.agent run    --email owner@clinic.com   # corrida manual, pide confirmación
    python -m app.cli.agent run    --yes                       # cron: sin identidad, sin confirmación
"""
from __future__ import annotations

import asyncio
import uuid
from functools import wraps

import click
from sqlalchemy import select

from app.core.crypto import make_search_hash
from app.db.session import AsyncSessionLocal
from app.models.user import User


def _async_command(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


async def _resolve_user(db, email: str, clinic_id: str | None) -> User:
    """Resuelve el usuario actual por email, igual que el login (auth.py:211-241)."""
    email_hash = make_search_hash(email.strip().lower())
    stmt = select(User).where(User.email_search_hash == email_hash)
    if clinic_id:
        try:
            clinic_uuid = uuid.UUID(clinic_id)
        except ValueError:
            raise click.ClickException(f"--clinic-id inválido: {clinic_id}")
        stmt = stmt.where(User.clinic_id == clinic_uuid)

    result = await db.execute(stmt)
    users = result.scalars().all()

    if not users:
        raise click.ClickException(f"No se encontró ningún usuario con email {email}.")
    if len(users) > 1:
        clinics = ", ".join(str(u.clinic_id) for u in users)
        raise click.ClickException(
            f"Ese email existe en varias clínicas ({clinics}). "
            f"Usa --clinic-id para elegir una."
        )
    return users[0]


def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    if not rows:
        click.echo("  (sin datos)")
        return
    widths = [
        max(len(str(headers[i])), *(len(str(r[i])) for r in rows))
        for i in range(len(headers))
    ]
    click.echo("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    click.echo("  ".join("-" * w for w in widths))
    for r in rows:
        click.echo("  ".join(str(v).ljust(widths[i]) for i, v in enumerate(r)))


@click.group()
def cli() -> None:
    """Agente de citas de Med-Sync — versión terminal."""


@cli.command()
@click.option("--email", required=True, help="Email del usuario (resuelve su clinic_id).")
@click.option("--clinic-id", default=None, help="UUID de clínica (solo si el email existe en varias).")
@_async_command
async def status(email: str, clinic_id: str | None) -> None:
    """Muestra próximas citas y recordatorios recientes de tu clínica."""
    from app.agent.scheduler import get_agent_status  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        user = await _resolve_user(db, email, clinic_id)
        data = await get_agent_status(db, clinic_id=user.clinic_id)

    click.echo(f"\nClínica: {user.clinic_id}")
    click.echo(f"Consultado: {data['checked_at']}")
    win_min, win_max = data["reminder_window_minutes"]
    click.echo(f"Ventana de recordatorio: {win_min}-{win_max} min antes\n")

    click.echo(f"Próximas citas (siguientes 3h) — {len(data['upcoming_appointments'])}")
    _print_table(
        ["Paciente", "Doctor", "Hora", "Min.", "Recordatorio", "Confirmada"],
        [
            [
                u["patient_name"], u["doctor_name"],
                u["starts_at"][11:16], f"{u['minutes_away']}m",
                "enviado" if u["reminder_sent"] else "pendiente",
                "sí" if u["patient_confirmed"] else "no",
            ]
            for u in data["upcoming_appointments"]
        ],
    )

    click.echo(f"\nNotificaciones últimas 24h — {len(data['recent_notifications'])}")
    _print_table(
        ["Canal", "Estado", "Enviado"],
        [
            [n["channel"], n["status"], n["sent_at"] or "-"]
            for n in data["recent_notifications"]
        ],
    )


@cli.command()
@click.option("--email", default=None, help="Opcional. Solo para trazar quién disparó una corrida manual.")
@click.option("--clinic-id", default=None, help="UUID de clínica (solo si --email existe en varias).")
@click.option("--yes", "-y", is_flag=True, help="No pedir confirmación. Requerido para uso en cron (sin --email).")
@_async_command
async def run(email: str | None, clinic_id: str | None, yes: bool) -> None:
    """Ejecuta el agente: envía recordatorios pendientes de TODAS las clínicas."""
    from app.agent.scheduler import run_all_tasks  # noqa: PLC0415

    async with AsyncSessionLocal() as db:
        who = "cron / sin identidad"
        if email:
            user = await _resolve_user(db, email, clinic_id)
            who = f"{email} (clínica {user.clinic_id})"

        if not yes:
            click.confirm(
                f"Vas a ejecutar el agente como {who}.\n"
                f"Esto envía recordatorios reales para TODAS las clínicas del sistema.\n"
                f"¿Continuar?",
                abort=True,
            )

        result = await run_all_tasks(db)

    click.echo(f"\nEjecutado: {result.ran_at.isoformat()}")
    click.echo(f"Recordatorios enviados: {result.reminders_sent}")
    click.echo(f"Recordatorios fallidos: {result.reminders_failed}")
    click.echo(f"No-shows marcados: {result.noshows_marked}")
    if result.errors:
        click.echo("\nErrores:")
        for e in result.errors:
            click.echo(f"  - {e}")


if __name__ == "__main__":
    cli()
