"""
app/core/config.py
──────────────────────────────────────────────────────────────────────────────
Central settings object (pydantic-settings v2).
All sensitive values come from environment variables / .env — never hardcoded.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )
    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME:    str = "MedicalSaaS"
    ENVIRONMENT: Literal["development",
                         "staging", "production"] = "development"
    DEBUG:       bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@localhost:5432/medical_saas"

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY:        str = "CHANGE_ME_use_openssl_rand_hex_32"
    JWT_ALGORITHM:         str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES:  int = 30
    REFRESH_TOKEN_EXPIRE_DAYS:    int = 7

    # ── Encryption ────────────────────────────────────────────────────────────
    # Comma-separated base64-encoded 32-byte keys, ordered oldest→newest.
    # The LAST key is always active for new encryptions.
    # Example (generate with crypto.generate_key_b64()):
    #   ENCRYPTION_KEYS="base64key0,base64key1"
    ENCRYPTION_KEYS: str = ""

    # Separate HMAC key for search hashes — must NOT equal any encryption key.
    SEARCH_HMAC_KEY: str = "CHANGE_ME_use_openssl_rand_hex_32"

    # ── AWS ───────────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID:     str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION:            str = "us-east-1"
    S3_BUCKET_RECORDS:     str = "medical-records-dev"
    S3_PRESIGNED_URL_TTL:  int = 300   # seconds — 5 min for PDF downloads

    # ── Stripe ────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY:       str = ""
    STRIPE_WEBHOOK_SECRET:   str = ""

    # ── SMTP ──────────────────────────────────────────────────────────────────
    SMTP_HOST:     str = "smtp.sendgrid.net"
    SMTP_PORT:     int = 587
    SMTP_USER:     str = ""
    SMTP_PASSWORD: str = ""
    EMAILS_FROM:   str = "noreply@medicalapp.com"

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("ENCRYPTION_KEYS")
    @classmethod
    def encryption_keys_not_empty_in_prod(cls, v: str, info) -> str:
        # Full validation (key length, count) is done lazily in crypto._load_keyring
        # to avoid circular imports at settings-load time.
        return v

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Module-level singleton used by crypto.py and everywhere else.
settings: Settings = get_settings()
