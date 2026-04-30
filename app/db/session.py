# app/db/session.py
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings

# Engine asíncrono (para FastAPI)
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    echo=settings.DEBUG,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# 👇 ENVOLVER el sync_engine en un bloque condicional
# para que no intente crearse si solo estamos importando para Alembic
_SYNC_ENGINE = None


def get_sync_engine():
    global _SYNC_ENGINE
    if _SYNC_ENGINE is None:
        from sqlalchemy import create_engine
        # Convertir asyncpg:// a postgresql:// para sync
        sync_url = settings.DATABASE_URL.replace(
            "postgresql+asyncpg://", "postgresql://")
        _SYNC_ENGINE = create_engine(sync_url)
    return _SYNC_ENGINE


# Para Alembic, expón esto
sync_engine = get_sync_engine()
