from app.api.routes.auth import router as auth_router
from app.api.routes.patients import router as patients_router
from app.api.routes.doctors import router as doctors_router
from app.api.routes.appointments import router as appointments_router
from app.api.routes.medical_records import router as medical_records_router
from app.api.routes.analytics import router as analytics_router
from app.api.routes.locations import router as locations_router
from app.api.routes.users import router as users_router
from app.api.routes.health import router as health_router
from app.api.routes.internal import router as internal_router
from app.api.routes.public_page import router as public_page_router
from app.api.routes.booking import router as booking_router
from app.api.routes.clinics import router as clinics_router
from app.api.routes.billing import router as billing_router
from app.api.routes.webhooks import router as webhooks_router
from app.api.routes.reschedule_requests import router as reschedule_requests_router

__all__ = [
    "auth_router", "patients_router", "doctors_router", "appointments_router",
    "medical_records_router", "analytics_router", "locations_router", "users_router",
    "health_router", "internal_router", "public_page_router", "booking_router",
    "clinics_router", "billing_router", "webhooks_router", "reschedule_requests_router"
]
