#!/usr/bin/env python3
"""
scripts/migrate_pdfs_to_s3.py
────────────────────────────────────────────────────────────────────────────────
Migra los PDFs de expedientes guardados en disco local a S3.

Antes de ejecutar este script, asegúrate de que:
  • .env.prod (o .env.dev) tenga AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    AWS_REGION y S3_BUCKET_RECORDS configurados.
  • El bucket de S3 exista y las credenciales tengan s3:PutObject.
  • La base de datos sea accesible desde esta máquina.

USO
  # Solo diagnóstico — no modifica nada
  APP_ENV=production python scripts/migrate_pdfs_to_s3.py --dry-run

  # Migrar todo (mantiene las copias locales por seguridad)
  APP_ENV=production python scripts/migrate_pdfs_to_s3.py

  # Migrar y eliminar archivos locales después de confirmar la subida
  APP_ENV=production python scripts/migrate_pdfs_to_s3.py --delete-local

COMPORTAMIENTO
  • Procesa solo los registros cuyo s3_pdf_key empieza con "uploads/" (local).
  • Para cada archivo:
      1. Lee el archivo del disco.
      2. Sube a S3 con SSE-AES256.
      3. Actualiza s3_pdf_key en la BD con la nueva key de S3.
      4. (Opcional) Elimina el archivo local.
  • Un fallo en un archivo no cancela el resto — continúa y reporta al final.
  • Idempotente: si ya fue migrado (key no empieza con "uploads/"), se omite.
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

# Add project root to PYTHONPATH so app.core.config is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3
from botocore.exceptions import BotoCoreError, ClientError
import sqlalchemy as sa
from sqlalchemy import create_engine, text

# ── Config ────────────────────────────────────────────────────────────────────

def _load_settings():
    from app.core.config import settings  # noqa: PLC0415
    return settings


# ── S3 helpers (sync — no asyncio needed in a script) ────────────────────────

def _s3_client(settings):
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )


def _upload(client, content: bytes, key: str, bucket: str) -> None:
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )


def _new_key(clinic_id: str, record_id: str, old_path: str) -> str:
    """Derive the S3 key from the old local path's filename."""
    filename = Path(old_path).name   # e.g. "20240101_120000123456.pdf"
    return f"medical-records/{clinic_id}/{record_id}/{filename}"


# ── DB helpers (sync SQLAlchemy) ──────────────────────────────────────────────

def _sync_db_url(async_url: str) -> str:
    """Convert asyncpg URL to psycopg2 for sync use in this script."""
    return async_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    dry_run      = "--dry-run"      in sys.argv
    delete_local = "--delete-local" in sys.argv

    settings = _load_settings()

    if not settings.AWS_ACCESS_KEY_ID or not settings.AWS_SECRET_ACCESS_KEY:
        print("ERROR: AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY no están configurados.", file=sys.stderr)
        sys.exit(1)

    print(f"Bucket  : {settings.S3_BUCKET_RECORDS}")
    print(f"Región  : {settings.AWS_REGION}")
    print(f"Dry-run : {dry_run}")
    print(f"Delete  : {delete_local}")
    print()

    engine = create_engine(_sync_db_url(settings.DATABASE_URL), future=True)
    client = _s3_client(settings)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id::text, clinic_id::text, s3_pdf_key "
                "FROM medical_records "
                "WHERE s3_pdf_key LIKE 'uploads/%' "
                "ORDER BY created_at"
            )
        ).fetchall()

    total   = len(rows)
    ok      = 0
    skipped = 0
    errors  = []

    print(f"Registros con path local: {total}")
    if total == 0:
        print("Nada que migrar.")
        return
    print()

    for record_id, clinic_id, local_key in rows:
        local_path = Path(local_key)
        s3_key     = _new_key(clinic_id, record_id, local_key)

        prefix = f"[{ok + skipped + len(errors) + 1}/{total}]"

        if not local_path.exists():
            print(f"{prefix} SKIP  {local_key!r}  — archivo no encontrado en disco")
            skipped += 1
            continue

        print(f"{prefix} {'DRY ' if dry_run else ''}→ s3://{settings.S3_BUCKET_RECORDS}/{s3_key}")

        if dry_run:
            ok += 1
            continue

        try:
            content = local_path.read_bytes()
            _upload(client, content, s3_key, settings.S3_BUCKET_RECORDS)
        except (BotoCoreError, ClientError, OSError) as exc:
            print(f"       ERROR upload: {exc}")
            errors.append((record_id, str(exc)))
            continue

        # Update DB
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE medical_records SET s3_pdf_key = :key WHERE id = :id::uuid"),
                {"key": s3_key, "id": record_id},
            )

        if delete_local:
            try:
                local_path.unlink()
                # Remove empty parent directories up to uploads/
                for parent in local_path.parents:
                    if parent.name == "uploads":
                        break
                    try:
                        parent.rmdir()
                    except OSError:
                        break
            except OSError as exc:
                print(f"       WARN  no se pudo eliminar {local_path}: {exc}")

        ok += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("═" * 60)
    print(f"Migrados exitosamente : {ok}")
    print(f"Omitidos (sin archivo): {skipped}")
    print(f"Errores               : {len(errors)}")
    if errors:
        print()
        print("Registros con error:")
        for rid, msg in errors:
            print(f"  {rid}: {msg}")
        sys.exit(1)

    if dry_run:
        print()
        print("Dry-run completado — no se realizaron cambios.")
        print("Ejecuta sin --dry-run para migrar.")


if __name__ == "__main__":
    main()
