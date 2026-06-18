from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.session import AsyncSessionLocal

router = APIRouter(tags=["Health"])


@router.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/health/db")
async def health_db():
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "up", "timestamp": timestamp}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "down", "timestamp": timestamp},
        )
