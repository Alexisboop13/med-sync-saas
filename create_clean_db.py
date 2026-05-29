import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
from app.models.base import Base

# Importa TODOS los modelos explícitamente
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

async def create_all():
    print("🔧 Conectando...")
    engine = create_async_engine(settings.DATABASE_URL, echo=False)

    async with engine.begin() as conn:
        # Reset total: elimina tablas, índices, secuencias y todo lo demás
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("GRANT ALL ON SCHEMA public TO public"))
        print("✅ Schema reseteado (tablas e índices eliminados)")

        # Extensiones requeridas
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        print("✅ Extensiones habilitadas (pg_trgm, btree_gist)")

        # Crea todo de nuevo
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Tablas creadas exitosamente")

        # Verificación
        result = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"))
        tables = result.fetchall()
        print("\n📋 Tablas creadas:")
        for table in tables:
            print(f"  ✓ {table[0]}")

    await engine.dispose()
    print("\n🎉 Listo!")

if __name__ == "__main__":
    asyncio.run(create_all())
