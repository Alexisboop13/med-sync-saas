"""
app/core/config.py
──────────────────────────────────────────────────────────────────────────────
Central settings object (pydantic-settings v2).

Selects the env file based on APP_ENV (set in the real shell environment):
  APP_ENV=development  →  .env.dev   (default)
  APP_ENV=staging      →  .env.staging
  APP_ENV=production   →  .env.prod

All sensitive values come from the env file — never hardcoded.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_ENV = os.getenv("APP_ENV", "development")

_ENV_FILE_MAP: dict[str, str] = {
    "development": ".env.dev",
    "staging": ".env.staging",
    "production": ".env.prod",
}

_ENV_FILE = _ENV_FILE_MAP.get(_APP_ENV, ".env.dev")

_INSECURE_PLACEHOLDERS = {
    "CHANGE_ME",
    "CHANGE_ME_openssl_rand_hex_32",
    "CHANGE_ME_use_openssl_rand_hex_32",
    "CHANGE_ME_base64_32bytes",
    "",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME:    str = "MedSync"
    PROJECT_NAME: str = "MedSync"
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG:       bool = False

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL:    str = "postgresql+asyncpg://user:pass@localhost:5432/medsync_db"
    DB_POOL_SIZE:    int = 5
    DB_MAX_OVERFLOW: int = 5

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY:               str = "CHANGE_ME_openssl_rand_hex_32"
    JWT_ALGORITHM:                str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES:  int = 30
    REFRESH_TOKEN_EXPIRE_DAYS:    int = 30

    # ── Encryption ────────────────────────────────────────────────────────────
    # Comma-separated base64-encoded 32-byte keys, ordered oldest→newest.
    # The LAST key is always active for new encryptions.
    ENCRYPTION_KEYS: str = ""

    # Separate HMAC key for search hashes — must NOT equal any encryption key.
    SEARCH_HMAC_KEY: str = "CHANGE_ME_openssl_rand_hex_32"

    # ── AWS ───────────────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID:     str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION:            str = "us-east-1"
    S3_BUCKET_RECORDS:     str = "medical-records-dev"
    S3_PRESIGNED_URL_TTL:  int = 300

    # ── Stripe ────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY:     str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    # Price IDs from Stripe Dashboard → Products → Prices
    STRIPE_PRICE_STARTER_MONTHLY:    str = ""
    STRIPE_PRICE_STARTER_ANNUAL:     str = ""
    STRIPE_PRICE_GROWTH_MONTHLY:     str = ""
    STRIPE_PRICE_GROWTH_ANNUAL:      str = ""
    STRIPE_PRICE_ENTERPRISE_MONTHLY: str = ""
    STRIPE_PRICE_ENTERPRISE_ANNUAL:  str = ""

    # ── Redis (rate limiting) ──────────────────────────────────────────────────
    # Empty = in-process memory (single-worker only).
    # Set to redis:// or rediss:// URL for multi-worker / production deployments.
    REDIS_URL: str = ""

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: str = "http://localhost:3000,https://medsync.clinic"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # ── Public base URL ───────────────────────────────────────────────────────
    APP_BASE_URL: str = "http://localhost:8000"

    # ── Reglas de negocio ─────────────────────────────────────────────────────
    PATIENT_CANCEL_HOURS_BEFORE: int = 1

    # ── SMTP ──────────────────────────────────────────────────────────────────
    SMTP_HOST:          str = ""
    SMTP_PORT:          int = 587
    SMTP_USER:          str = ""
    SMTP_PASSWORD:      str = ""
    EMAILS_FROM:        str = "noreply@medsync.com"
    SMTP_TLS:           bool = True
    CLINIC_NOTIFY_EMAIL: str = ""  # Inbox that receives patient reschedule requests

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("ENCRYPTION_KEYS")
    @classmethod
    def encryption_keys_not_empty_in_prod(cls, v: str, info) -> str:
        return v

    @model_validator(mode="after")
    def block_insecure_secrets_in_production(self) -> "Settings":
        if self.ENVIRONMENT != "production":
            return self
        guarded = {
            "JWT_SECRET_KEY": self.JWT_SECRET_KEY,
            "ENCRYPTION_KEYS": self.ENCRYPTION_KEYS,
            "SEARCH_HMAC_KEY": self.SEARCH_HMAC_KEY,
        }
        for field, value in guarded.items():
            if value in _INSECURE_PLACEHOLDERS:
                raise ValueError(
                    f"{field} contiene un valor placeholder — "
                    "reemplázalo con un secreto real antes de iniciar en producción."
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
