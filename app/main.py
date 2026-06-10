from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from app.core.config import settings
from app.api.routes import (
    auth_router,
    patients_router,
    doctors_router,
    appointments_router,
    medical_records_router,
    analytics_router,
    locations_router,
    users_router,
    health_router,
    internal_router,
    public_page_router,
    booking_router,
    verify_router,
    clinics_router,
    billing_router,
    webhooks_router,
    reschedule_requests_router,
)
from app.core.limiter import limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.db.session import engine
from app.models.base import Base

_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
    description="Backend para Med-Sync: Gestión Dental SaaS",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Public routes (no JWT required) ─────────────────────────────────────────
# GET /health, /health/db
app.include_router(health_router)
# HTML pages (no prefix)
app.include_router(public_page_router)
# /api/v1/public/book/*
app.include_router(booking_router, prefix="/api/v1")
# /api/v1/public/verify/*
app.include_router(verify_router, prefix="/api/v1")

# ── Authenticated routes ─────────────────────────────────────────────────────
app.include_router(auth_router, prefix="/api/v1")
app.include_router(patients_router, prefix="/api/v1")
app.include_router(doctors_router, prefix="/api/v1")
app.include_router(appointments_router, prefix="/api/v1")
app.include_router(medical_records_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(locations_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(clinics_router, prefix="/api/v1")
app.include_router(billing_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(reschedule_requests_router, prefix="/api/v1")


@app.get("/app", include_in_schema=False)
async def admin_frontend():
    return FileResponse(_ROOT / "index.html", media_type="text/html")


@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tablas creadas/verificadas en startup")
    from app.agent.background import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def shutdown_event():
    from app.agent.background import stop_scheduler
    stop_scheduler()


app.include_router(internal_router)
