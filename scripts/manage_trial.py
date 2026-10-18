"""
scripts/manage_trial.py
──────────────────────────────────────────────────────────────────────────────
Administra el período de prueba y el plan de las clínicas directamente en la
base de datos (sin pasar por HTTP ni JWT).

Uso:
    python -m scripts.manage_trial list
    python -m scripts.manage_trial start <slug> [--days N]
    python -m scripts.manage_trial extend <slug> --days N
    python -m scripts.manage_trial activate <slug> [--plan starter|growth|enterprise]
    python -m scripts.manage_trial suspend <slug>
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models.clinic import Clinic, PlanTier, SubscriptionStatus


async def _get_clinic(db, slug: str) -> Clinic:
    result = await db.execute(select(Clinic).where(Clinic.slug == slug))
    clinic = result.scalar_one_or_none()
    if clinic is None:
        raise SystemExit(f"No existe ninguna clínica con slug '{slug}'.")
    return clinic


async def cmd_list(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Clinic).order_by(Clinic.name))
        clinics = result.scalars().all()

    if not clinics:
        print("(sin clínicas)")
        return

    headers = ["slug", "estado", "plan", "prueba_hasta", "bloqueada"]
    rows = []
    for c in clinics:
        trial_hasta = c.trial_ends_at.isoformat() if c.trial_ends_at else "-"
        rows.append([c.slug, c.subscription_status, c.plan_tier, trial_hasta, "sí" if c.is_suspended else "no"])

    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v.ljust(widths[i]) for i, v in enumerate(r)))


async def cmd_start(args: argparse.Namespace) -> None:
    days = args.days if args.days is not None else settings.TRIAL_DAYS
    async with AsyncSessionLocal() as db:
        clinic = await _get_clinic(db, args.slug)
        clinic.subscription_status = SubscriptionStatus.TRIALING
        clinic.trial_ends_at = datetime.now(timezone.utc) + timedelta(days=days)
        clinic.past_due_since = None
        await db.commit()
        print(f"✓  Prueba iniciada para '{args.slug}' — termina el {clinic.trial_ends_at.isoformat()}.")


async def cmd_extend(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        clinic = await _get_clinic(db, args.slug)
        now = datetime.now(timezone.utc)
        base = clinic.trial_ends_at if (clinic.trial_ends_at and clinic.trial_ends_at > now) else now
        clinic.trial_ends_at = base + timedelta(days=args.days)
        if clinic.subscription_status != SubscriptionStatus.TRIALING:
            clinic.subscription_status = SubscriptionStatus.TRIALING
        await db.commit()
        print(f"✓  Prueba de '{args.slug}' extendida hasta el {clinic.trial_ends_at.isoformat()}.")


async def cmd_activate(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        clinic = await _get_clinic(db, args.slug)
        clinic.subscription_status = SubscriptionStatus.ACTIVE
        clinic.trial_ends_at = None
        clinic.past_due_since = None
        if args.plan:
            clinic.plan_tier = PlanTier(args.plan)
        await db.commit()
        print(f"✓  '{args.slug}' activada — plan: {clinic.plan_tier}.")


async def cmd_suspend(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        clinic = await _get_clinic(db, args.slug)
        clinic.subscription_status = SubscriptionStatus.SUSPENDED
        await db.commit()
        print(f"✓  '{args.slug}' suspendida.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.manage_trial",
        description="Administra el período de prueba y el plan de las clínicas.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="Lista todas las clínicas con su estado de prueba/plan.")

    p_start = sub.add_parser("start", help="Inicia (o reinicia) el período de prueba de una clínica.")
    p_start.add_argument("slug")
    p_start.add_argument("--days", type=int, default=None, help=f"Default: TRIAL_DAYS ({settings.TRIAL_DAYS}).")

    p_extend = sub.add_parser("extend", help="Extiende el período de prueba.")
    p_extend.add_argument("slug")
    p_extend.add_argument("--days", type=int, required=True)

    p_activate = sub.add_parser("activate", help="Activa la clínica (plan de pago).")
    p_activate.add_argument("slug")
    p_activate.add_argument("--plan", choices=["starter", "growth", "enterprise"], default=None)

    p_suspend = sub.add_parser("suspend", help="Suspende manualmente el acceso de la clínica.")
    p_suspend.add_argument("slug")

    return parser


_COMMANDS = {
    "list": cmd_list,
    "start": cmd_start,
    "extend": cmd_extend,
    "activate": cmd_activate,
    "suspend": cmd_suspend,
}


async def _main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        await _COMMANDS[args.command](args)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
