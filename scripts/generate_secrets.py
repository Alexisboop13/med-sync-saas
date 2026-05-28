#!/usr/bin/env python3
"""
scripts/generate_secrets.py
────────────────────────────────────────────────────────────────────────────────
Genera los tres secretos criptográficos que necesita Med-Sync en producción.
No requiere dependencias externas — solo stdlib.

USO
  python scripts/generate_secrets.py              # imprime en pantalla
  python scripts/generate_secrets.py --write      # escribe en .env.prod (seguro: falla si ya existe)
  python scripts/generate_secrets.py --rotate     # agrega una segunda clave de cifrado (rotación)

PROCESO RECOMENDADO PARA UN DEPLOY NUEVO
  1. python scripts/generate_secrets.py --write
  2. Abrir .env.prod y completar las variables marcadas con COMPLETAR
  3. Copiar .env.prod a la ubicación segura del servidor (nunca al repo)
  4. APP_ENV=production uvicorn app.main:app

ROTACIÓN DE CLAVE DE CIFRADO (sin downtime)
  1. python scripts/generate_secrets.py --rotate
     → El script agrega la nueva clave al final de ENCRYPTION_KEYS en .env.prod
  2. Redeploy → el app cifra con la nueva clave, descifra registros viejos con la anterior
  3. Ejecutar el job de re-cifrado cuando la carga sea baja (ver docs)
  4. Una vez re-cifrado todo, eliminar la clave vieja de ENCRYPTION_KEYS
"""

from __future__ import annotations

import base64
import os
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path

_ENV_FILE = Path(".env.prod")


# ── Generadores ───────────────────────────────────────────────────────────────

def _gen_hex_32() -> str:
    """64 caracteres hexadecimales (256 bits de entropía)."""
    return secrets.token_hex(32)


def _gen_encryption_key() -> str:
    """32 bytes aleatorios en base64 estándar (formato que espera app/core/crypto.py)."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


# ── Acciones ──────────────────────────────────────────────────────────────────

def cmd_print() -> None:
    """Imprime los secretos generados listos para pegar en .env.prod."""
    jwt_key  = _gen_hex_32()
    hmac_key = _gen_hex_32()
    enc_key  = _gen_encryption_key()

    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"""
# ── Secretos generados el {ts} ────────────────────────────────────────────────
# ⚠  Guarda estos valores en un gestor de secretos (Vault, AWS SSM, 1Password).
# ⚠  NUNCA los commitas al repositorio. .gitignore ya excluye .env.prod.

JWT_SECRET_KEY={jwt_key}
SEARCH_HMAC_KEY={hmac_key}
ENCRYPTION_KEYS={enc_key}
""")
    print("─" * 72)
    print("Pega los tres valores en tu .env.prod y completa el resto.")
    print("Para escribir directamente:  python scripts/generate_secrets.py --write")


def cmd_write() -> None:
    """Genera secretos y crea .env.prod. Falla si el archivo ya existe."""
    if _ENV_FILE.exists():
        _abort(
            f"{_ENV_FILE} ya existe.\n"
            "  • Para regenerar secretos en un archivo nuevo, bórralo primero.\n"
            "  • Para agregar una segunda clave de cifrado: --rotate"
        )

    jwt_key  = _gen_hex_32()
    hmac_key = _gen_hex_32()
    enc_key  = _gen_encryption_key()
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M")

    template = _env_template(jwt_key=jwt_key, hmac_key=hmac_key, enc_key=enc_key, ts=ts)
    _ENV_FILE.write_text(template, encoding="utf-8")
    _ENV_FILE.chmod(0o600)   # solo lectura para el propietario del proceso
    print(f"✓  Secretos escritos en {_ENV_FILE}  (permisos 600)")
    print("   Completa las variables marcadas con COMPLETAR antes de desplegar.")


def cmd_rotate() -> None:
    """Agrega una nueva clave de cifrado al final de ENCRYPTION_KEYS en .env.prod."""
    if not _ENV_FILE.exists():
        _abort(f"{_ENV_FILE} no existe. Ejecuta --write primero.")

    content = _ENV_FILE.read_text(encoding="utf-8")

    match = re.search(r"^ENCRYPTION_KEYS=(.+)$", content, re.MULTILINE)
    if not match:
        _abort("No se encontró ENCRYPTION_KEYS en .env.prod.")

    current_keys = match.group(1).strip()
    new_key      = _gen_encryption_key()
    updated_keys = f"{current_keys},{new_key}"

    new_content = content[:match.start(1)] + updated_keys + content[match.end(1):]
    _ENV_FILE.write_text(new_content, encoding="utf-8")

    print(f"✓  Nueva clave de cifrado agregada a ENCRYPTION_KEYS en {_ENV_FILE}")
    print(f"   Nueva clave: {new_key}")
    print()
    print("PRÓXIMOS PASOS:")
    print("  1. Redeploy → el app cifra nuevos registros con la nueva clave.")
    print("  2. Ejecuta el job de re-cifrado para migrar registros viejos.")
    print("  3. Una vez re-cifrado todo, elimina la clave anterior de ENCRYPTION_KEYS.")


# ── Template ──────────────────────────────────────────────────────────────────

def _env_template(jwt_key: str, hmac_key: str, enc_key: str, ts: str) -> str:
    return f"""\
# ────────────────────────────────────────────────────────────────────────────────
# .env.prod  —  generado el {ts}
# ⚠  Este archivo contiene secretos de producción. NUNCA lo commitas al repo.
#    .gitignore ya lo excluye. Guarda una copia en tu gestor de secretos.
# ────────────────────────────────────────────────────────────────────────────────

# ── App ──────────────────────────────────────────────────────────────────────
APP_NAME=MedSync
ENVIRONMENT=production
DEBUG=False


# ── Base de datos ─────────────────────────────────────────────────────────────
# Formato: postgresql+asyncpg://usuario:contraseña@host:5432/nombre_db
# En RDS/Supabase/Neon: obtener la connection string del panel y agregar +asyncpg
DATABASE_URL=postgresql+asyncpg://medsync:COMPLETAR@COMPLETAR:5432/medsync_prod
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=10


# ── JWT ───────────────────────────────────────────────────────────────────────
JWT_SECRET_KEY={jwt_key}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=7


# ── Cifrado de campos PII ─────────────────────────────────────────────────────
# Para rotar claves: python scripts/generate_secrets.py --rotate
ENCRYPTION_KEYS={enc_key}
SEARCH_HMAC_KEY={hmac_key}


# ── URLs ──────────────────────────────────────────────────────────────────────
# APP_BASE_URL se usa en los enlaces de email (confirmación, reset de contraseña)
APP_BASE_URL=https://COMPLETAR.com


# ── CORS ──────────────────────────────────────────────────────────────────────
# Lista separada por coma, sin espacios. Solo incluir dominios que sirvan el frontend.
CORS_ORIGINS=https://COMPLETAR.com


# ── SMTP (Resend — recomendado para transaccional) ────────────────────────────
# Resend: crear API key en resend.com → Settings → API Keys
# Host: smtp.resend.com  Puerto: 587  Usuario: resend  Password: re_xxxx
SMTP_HOST=smtp.resend.com
SMTP_PORT=587
SMTP_USER=resend
SMTP_PASSWORD=COMPLETAR
SMTP_TLS=True
EMAILS_FROM=noreply@COMPLETAR.com
CLINIC_NOTIFY_EMAIL=COMPLETAR@COMPLETAR.com


# ── Reglas de negocio ─────────────────────────────────────────────────────────
PATIENT_CANCEL_HOURS_BEFORE=2


# ── AWS (dejar vacíos para deshabilitar S3) ───────────────────────────────────
# Necesario si usas almacenamiento de PDFs en S3/R2 en lugar del disco local
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_REGION=us-east-1
S3_BUCKET_RECORDS=medsync-medical-records-prod
S3_PRESIGNED_URL_TTL=300


# ── Stripe (dejar vacíos para deshabilitar billing) ───────────────────────────
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
"""


# ── Utilidades ────────────────────────────────────────────────────────────────

def _abort(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ── Entrypoint ────────────────────────────────────────────────────────────────

def main() -> None:
    args = set(sys.argv[1:])

    if "--rotate" in args:
        cmd_rotate()
    elif "--write" in args:
        cmd_write()
    elif not args or "--help" in args or "-h" in args:
        cmd_print()
    else:
        _abort(f"Argumento desconocido: {' '.join(args)}\nUso: --write | --rotate | (sin args)")


if __name__ == "__main__":
    main()
