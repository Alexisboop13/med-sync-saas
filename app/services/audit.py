"""
app/services/audit.py
──────────────────────────────────────────────────────────────────────────────
Fire-and-forget audit writer.

Design decisions:
  • log_audit() NEVER raises — a failed audit write must never break the caller.
  • Does NOT commit by default. The caller's own commit includes the audit row.
    Pass commit=True for error paths (login_failed) or read-only paths
    (download_pdf) where the caller never commits.
  • Payload follows the CloudEvents 1.0 envelope defined in audit_log.py.
  • PII rule: field values ending in _enc are listed in `masked_fields`, never
    in `before` / `after`. Only non-sensitive scalars (status, role, dates) may
    appear in those dicts.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

log = logging.getLogger(__name__)


async def log_audit(
    db: AsyncSession,
    *,
    event_type: str,
    entity_type: str,
    clinic_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    actor_role: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    source: str = "",
    data: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    commit: bool = False,
) -> None:
    """
    Append a CloudEvents-compatible audit record to *db*.

    commit=False (default): the caller's own db.commit() will persist this row.
    commit=True: commit immediately (use in exception/read-only paths).
    """
    try:
        entry_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        payload: dict[str, Any] = {
            "specversion": "1.0",
            "id": str(entry_id),
            "source": source,
            "type": event_type,
            "subject": str(entity_id) if entity_id else None,
            "time": now.isoformat(),
            "datacontenttype": "application/json",
            "data": data or {},
        }
        entry = AuditLog(
            id=entry_id,
            clinic_id=clinic_id,
            actor_id=actor_id,
            actor_role=actor_role,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            occurred_at=now,
            ip_address=ip[:45] if ip else None,
            user_agent=user_agent[:300] if user_agent else None,
            payload=payload,
        )
        db.add(entry)
        if commit:
            await db.commit()
    except Exception:
        log.error("audit.log_audit failed event=%s entity=%s", event_type, entity_type, exc_info=True)
