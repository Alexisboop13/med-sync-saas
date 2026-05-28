import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.config import settings
from app.core.limiter import limiter
from app.api.routes.auth import router as auth_router
from app.api.routes.patients import router as patients_router
from app.api.routes.appointments import router as appointments_router
from app.api.routes.doctors import router as doctors_router
from app.api.routes.medical_records import router as medical_records_router
from app.api.routes.public_page import router as public_page_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.locations import router as locations_router
from app.api.routes.health import router as health_router
from app.api.routes.internal import router as internal_router
from app.api.routes.users import router as users_router
from app.api.routes.reschedule_requests import router as reschedule_requests_router
from app.api.routes.booking import router as booking_router
from app.api.routes.verify import router as verify_router

logger = logging.getLogger(__name__)

# Origins that are always allowed in development regardless of env file.
_DEV_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5500",   # VS Code Live Server
    "http://127.0.0.1:5500",
    "http://localhost:8080",
    "null",                    # file:// requests
]


def get_application() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        description="Backend para Med-Sync: Gestión Dental SaaS",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    application.state.limiter = limiter
    application.add_exception_handler(
        RateLimitExceeded, _rate_limit_exceeded_handler)
    application.add_middleware(SlowAPIMiddleware)

    # Build the allowed-origins list.  In development we merge the env list
    # with _DEV_ORIGINS so any local file/server setup works out of the box.
    _env_origins = settings.cors_origins_list
    if settings.is_production:
        _allowed_origins = _env_origins
    else:
        _allowed_origins = list(dict.fromkeys(_env_origins + _DEV_ORIGINS))

    application.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Catch-all handler for unhandled exceptions.
    #
    # Without this, unhandled exceptions escape past CORSMiddleware and are
    # caught by Starlette's ServerErrorMiddleware, which sends the 500 response
    # *directly* — bypassing CORSMiddleware's send-wrapper and therefore
    # omitting the Access-Control-Allow-Origin header.  The browser then reports
    # a CORS error instead of the actual 500, which is confusing to debug.
    #
    # By registering this handler at the FastAPI/ExceptionMiddleware level it
    # stays *inside* CORSMiddleware, so the response goes through the normal
    # send path and gets its CORS headers.
    @application.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "Unhandled %s: %s %s",
            type(exc).__name__,
            request.method,
            request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Error interno del servidor. Revisa los logs del backend."},
        )

    application.include_router(auth_router, prefix="/api/v1")
    application.include_router(patients_router, prefix="/api/v1")
    application.include_router(appointments_router, prefix="/api/v1")
    application.include_router(doctors_router, prefix="/api/v1")
    application.include_router(medical_records_router, prefix="/api/v1")
    application.include_router(public_page_router)
    application.include_router(analytics_router, prefix="/api/v1")
    application.include_router(locations_router, prefix="/api/v1")
    application.include_router(health_router)
    application.include_router(internal_router)
    application.include_router(users_router, prefix="/api/v1")
    application.include_router(reschedule_requests_router, prefix="/api/v1")
    application.include_router(booking_router, prefix="/api/v1")
    application.include_router(verify_router, prefix="/api/v1")

    @application.get("/", tags=["Health Check"])
    async def root():
        return {
            "status": "online",
            "message": "Bienvenido a la API de Med-Sync",
            "docs": "/docs",
        }

    return application


app = get_application()
