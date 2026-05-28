"""
app/core/limiter.py
──────────────────────────────────────────────────────────────────────────────
Rate-limiting setup via slowapi + limits library.

Storage backend:
  REDIS_URL set  → shared Redis counter, safe across multiple workers.
  REDIS_URL empty → in-process MemoryStorage (development / single-worker only).

With in_memory_fallback_enabled=True, if Redis becomes unreachable at runtime
the limiter degrades gracefully to per-process counters instead of failing the
request with 500.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

log = logging.getLogger(__name__)


def _safe_redis_url(url: str) -> str:
    """Return the Redis URL with credentials redacted for log messages."""
    try:
        p = urlparse(url)
        host_port = f"{p.hostname}:{p.port}" if p.port else (p.hostname or url)
        return f"{p.scheme}://{host_port}"
    except Exception:
        return "<redis>"


if settings.REDIS_URL:
    _storage_uri = settings.REDIS_URL
    log.info("Rate limiter: Redis backend (%s)", _safe_redis_url(settings.REDIS_URL))
else:
    _storage_uri = "memory://"
    log.info("Rate limiter: in-memory backend — set REDIS_URL for multi-worker deployments")


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["100/minute"],
    storage_uri=_storage_uri,
    # Degrade to per-process memory if Redis is configured but temporarily
    # unreachable, rather than returning 500 to every request.
    in_memory_fallback_enabled=bool(settings.REDIS_URL),
)


def get_ip_and_clinic(request: Request) -> str:
    """
    Rate-limit key that combines IP + clinic_id from the JWT.

    This prevents a single clinic from consuming another clinic's quota
    even when both share the same egress IP (e.g. corporate NAT).
    Falls back to bare IP if the token is absent or invalid.
    """
    ip = get_remote_address(request)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": False},
            )
            clinic_id = payload.get("clinic_id")
            if clinic_id:
                return f"{ip}:{clinic_id}"
        except jwt.PyJWTError:
            pass
    return ip
