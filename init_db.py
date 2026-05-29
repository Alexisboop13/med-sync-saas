import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
from app.models.base import Base

# Importar TODOS los modelos (importante para que Base.metadata los reconozca)
from app.models.clinic import Clinic
from app.models.user import User
from app.models.doctor import Doctor
from app.models.patient import Patient
from app.models.location import Location
from app.models.appointment import Appointment
from app.models.medical_record import MedicalRecord
from app.models.audit_log import AuditLog
from app.models.refresh_token import RefreshToken
from app.models.password_reset_token import PasswordResetToken
from app.models.reschedule_request import RescheduleRequest
from app.models.notification import Notification
from app.models.appointment_note import AppointmentNote
from app.models.email_verification import EmailVerification

async def main():
    print("🔧 Conectando a la base de datos...")
    engine = create_async_engine(settings.DATABASE_URL, echo=True)

    print("📊 Creando tablas...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("✅ Tablas creadas exitosamente")

    # Verificar
    print("\n📋 Verificando tablas creadas:")
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))
        rows = await result.fetchall()
        for row in rows:
            print(f"  ✓ {row[0]}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
