from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings


def get_application() -> FastAPI:
    application = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        description="Backend para Med-Sync: Gestión Dental SaaS",
    )

    # Configuración de CORS (Vital para que tu Novia o Clientes puedan probar el Front)
    application.add_middleware(
        CORSMiddleware,
        # En producción cambiaremos esto por el dominio real
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/", tags=["Health Check"])
    async def root():
        return {
            "status": "online",
            "message": "Bienvenido a la API de Med-Sync",
            "docs": "/docs"
        }

    return application


app = get_application()
