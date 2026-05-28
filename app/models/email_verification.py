from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import SystemBase


class EmailVerification(SystemBase):
    """
    Per-email verification record for the public booking flow.

    One row per email address (enforced via UNIQUE on email_hash).
    Rate limiting: max 3 codes per email per 1-hour window tracked by send_count + created_at.

    Flow:
      1. POST /public/verify/send-code   → sets code + expires_at (15 min TTL)
      2. POST /public/verify/check-code  → sets verified_at + verification_token (30 min TTL)
      3. POST /public/book/{slug}         → validates token + email_hash, then invalidates token
    """

    email_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        comment="HMAC-SHA256 of lower(email.strip()). Never stores the raw email.",
    )

    code: Mapped[str] = mapped_column(
        String(6),
        nullable=False,
        comment="6-digit zero-padded numeric code.",
    )

    send_count: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default="1",
        comment=(
            "Codes sent in the current 1-hour window. "
            "Reset to 1 (and created_at reset to now) when the window expires."
        ),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Code expiry. Typically created_at + 15 min.",
    )

    verification_token: Mapped[Optional[str]] = mapped_column(
        String(36),
        nullable=True,
        unique=True,
        index=True,
        comment="UUID4 returned after successful code verification. Single-use; expires in 30 min.",
    )

    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="verification_token expiry. Set to now + 30 min on successful check-code.",
    )

    verified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When the code was successfully verified. NULL = not yet verified.",
    )

    def __repr__(self) -> str:
        return (
            f"<EmailVerification hash=...{self.email_hash[-6:]} "
            f"verified={self.verified_at is not None}>"
        )
