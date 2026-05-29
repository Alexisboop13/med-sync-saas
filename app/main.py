from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
app.include_router(health_router)                        # GET /health, /health/db
app.include_router(public_page_router)                   # HTML pages (no prefix)
app.include_router(booking_router, prefix="/api/v1")     # /api/v1/public/book/*
app.include_router(verify_router, prefix="/api/v1")      # /api/v1/public/verify/*

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
app.include_router(internal_router)
