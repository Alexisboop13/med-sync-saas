"""
app/core/s3.py
────────────────────────────────────────────────────────────────────────────────
Async S3 wrapper for PDF storage.

All public functions are async; they delegate synchronous boto3 calls to a
thread-pool via asyncio.to_thread so they don't block the event loop.

A single boto3 client is created per call — boto3 clients are cheap to
instantiate (no TCP connection at construction time) and are NOT thread-safe
when shared, so a fresh one per call is the safest approach for a thread pool.

Backward-compatibility note
─────────────────────────────
Records uploaded before this module existed have s3_pdf_key values that look
like local paths (e.g. "uploads/medical-records/…").  The download endpoint
in medical_records.py detects these by the "uploads/" prefix and serves them
from disk.  The migration script (scripts/migrate_pdfs_to_s3.py) converts
them to S3 keys so the local-fallback code path can eventually be removed.

S3 key format
──────────────
    medical-records/{clinic_id}/{record_id}/{YYYYMMDD_HHMMSSffffff}.pdf

All objects are stored with SSE-S3 (AES-256) server-side encryption.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException, status

from app.core.config import settings

log = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    """True when AWS credentials are present in settings."""
    return bool(settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY)


def build_key(clinic_id: str, record_id: str) -> str:
    """Return a deterministic, sortable S3 object key for a new PDF upload."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S%f")
    return f"medical-records/{clinic_id}/{record_id}/{ts}.pdf"


def _client():
    return boto3.client(
        "s3",
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_REGION,
    )


def _require_configured() -> None:
    if not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "El almacenamiento de archivos no está configurado. "
                "Contacta al administrador del sistema."
            ),
        )


# ── Sync implementations ──────────────────────────────────────────────────────

def _sync_upload(content: bytes, key: str) -> None:
    _client().put_object(
        Bucket=settings.S3_BUCKET_RECORDS,
        Key=key,
        Body=content,
        ContentType="application/pdf",
        ServerSideEncryption="AES256",
    )


def _sync_presigned_url(key: str, ttl: int, disposition: str) -> str:
    return _client().generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.S3_BUCKET_RECORDS,
            "Key": key,
            "ResponseContentDisposition": disposition,
            "ResponseContentType": "application/pdf",
        },
        ExpiresIn=ttl,
    )


def _sync_delete(key: str) -> None:
    _client().delete_object(Bucket=settings.S3_BUCKET_RECORDS, Key=key)


# ── Public async API ──────────────────────────────────────────────────────────

async def upload_file(content: bytes, key: str) -> str:
    """
    Upload PDF bytes to S3 with SSE-S3 encryption.
    Returns the key on success; raises HTTP 503/502 on misconfiguration/failure.
    """
    _require_configured()
    try:
        await asyncio.to_thread(_sync_upload, content, key)
        return key
    except (BotoCoreError, ClientError) as exc:
        log.error("S3 upload failed key=%s: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Error al subir el archivo. Intenta de nuevo.",
        ) from exc


async def presigned_download_url(
    key: str,
    filename: str = "record.pdf",
    inline: bool = True,
) -> str:
    """
    Generate a time-limited presigned GET URL.

    The URL embeds Content-Disposition (inline or attachment) so the browser
    opens or downloads the PDF without any extra headers from the client.
    TTL comes from settings.S3_PRESIGNED_URL_TTL (default 300 s).
    """
    _require_configured()
    disposition = f"{'inline' if inline else 'attachment'}; filename=\"{filename}\""
    ttl = settings.S3_PRESIGNED_URL_TTL
    try:
        return await asyncio.to_thread(_sync_presigned_url, key, ttl, disposition)
    except (BotoCoreError, ClientError) as exc:
        log.error("S3 presign failed key=%s: %s", key, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="No se pudo generar el enlace de descarga.",
        ) from exc


async def delete_file(key: str) -> None:
    """
    Delete an S3 object.  Logs but does NOT raise on failure — a missing object
    should never block the deletion of the DB record that referenced it.
    """
    if not is_configured():
        return
    try:
        await asyncio.to_thread(_sync_delete, key)
    except (BotoCoreError, ClientError) as exc:
        log.error("S3 delete failed key=%s: %s", key, exc)
