"""
scripts/reset_password.py
──────────────────────────────────────────────────────────────────────────────
Restablece la contraseña de un usuario a mano, para usar desde el Shell de
Render cuando no hay correo configurado (SMTP_HOST/SMTP_USER vacíos) y
"¿Olvidaste tu contraseña?" no puede enviar nada.

- Genera una contraseña temporal segura (secrets.token_urlsafe) y la guarda
  con el mismo hash Argon2 que usa el login normal (get_password_hash).
- Revoca todos los refresh tokens activos del usuario (fuerza re-login en
  cualquier sesión abierta).
- Registra un AuditLog (USER_PASSWORD_RESET_ADMIN).
- Imprime la contraseña temporal UNA SOLA VEZ — no se guarda en ningún
  lado. Pásala al usuario por un canal seguro (no email, no quedó
  configurado) y pídele que la cambie en cuanto entre.

Uso:
    python -m scripts.reset_password owner@clinica.com
    python -m scripts.reset_password owner@clinica.com --clinic-id <uuid>   # si el email existe en varias clínicas
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.crypto import make_search_hash
from app.core.security import get_password_hash
from app.db.session import AsyncSessionLocal, engine
from app.models.audit_log import EventType
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.audit import log_audit


async def _resolve_user(db, email: str, clinic_id: str | None) -> User:
    email_hash = make_search_hash(email.strip().lower())
    stmt = select(User).where(User.email_search_hash == email_hash)
    if clinic_id:
        try:
            clinic_uuid = uuid.UUID(clinic_id)
        except ValueError:
            raise SystemExit(f"--clinic-id inválido: {clinic_id}")
        stmt = stmt.where(User.clinic_id == clinic_uuid)

    result = await db.execute(stmt)
    users = result.scalars().all()

    if not users:
        raise SystemExit(f"No se encontró ningún usuario con email {email}.")
    if len(users) > 1:
        clinics = ", ".join(str(u.clinic_id) for u in users)
        raise SystemExit(
            f"Ese email existe en varias clínicas ({clinics}). Usa --clinic-id para elegir una."
        )
    return users[0]


async def main(email: str, clinic_id: str | None) -> None:
    async with AsyncSessionLocal() as db:
        user = await _resolve_user(db, email, clinic_id)

        temp_password = secrets.token_urlsafe(12)
        user.hashed_password = get_password_hash(temp_password)

        revoked = await db.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
        now = datetime.now(timezone.utc)
        count = 0
        for token in revoked.scalars().all():
            token.revoked_at = now
            count += 1

        await log_audit(
            db,
            event_type=EventType.USER_PASSWORD_RESET_ADMIN,
            entity_type="User",
            clinic_id=user.clinic_id,
            entity_id=user.id,
            source="scripts/reset_password.py",
            data={"revoked_refresh_tokens": count},
        )
        await db.commit()

        print(f"✓ Contraseña restablecida para {email} (clínica {user.clinic_id}).")
        print(f"  Sesiones activas cerradas: {count}")
        print()
        print(f"  Contraseña temporal: {temp_password}")
        print()
        print("  Pásasela al usuario por un canal seguro (no se guarda en ningún lado,")
        print("  ni aquí ni en la base). Pídele que la cambie en cuanto entre.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="python -m scripts.reset_password",
        description="Restablece la contraseña de un usuario a mano (cuando no hay email configurado).",
    )
    parser.add_argument("email")
    parser.add_argument("--clinic-id", default=None, help="UUID de clínica (solo si el email existe en varias).")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.clinic_id))
