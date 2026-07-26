from app.models.notification import Notification
from app.models.audit_log import AuditLog
from app.models.medical_record import MedicalRecord
from app.models.appointment import Appointment
from app.models.patient import Patient
from app.models.doctor import Doctor
from app.models.user import User
from app.models.clinic import Clinic
from app.models.base import Base
from sqlalchemy import create_engine
from alembic import context
from logging.config import fileConfig
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

# Asegurar que encuentra la carpeta 'app'
sys.path.insert(0, str(Path(__file__).parent.parent))


# IMPORTAR LA BASE Y TODOS LOS MODELOS EXPLÍCITAMENTE

# IMPORTAR CADA MODELO PARA QUE SE REGISTREN EN Base.metadata

# VERIFICAR QUE SE CARGARON LAS TABLAS
print("🔍 Tablas detectadas por Alembic:")
for table_name in Base.metadata.tables.keys():
    print(f"   - {table_name}")

target_metadata = Base.metadata

# Configuración desde alembic.ini
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _to_sync_url(async_url: str) -> str:
    """Convierte una DATABASE_URL de asyncpg a una URL síncrona para Alembic.

    asyncpg acepta el parámetro de query `ssl=`, pero el driver síncrono
    (psycopg2) requiere `sslmode=` — si no se traduce, psycopg2 falla con
    'invalid dsn: invalid connection option "ssl"' en cualquier URL que
    incluya `?ssl=...` (p. ej. Neon).
    """
    url = async_url.replace("postgresql+asyncpg://", "postgresql://")
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if "ssl" in query:
        ssl_value = query.pop("ssl")[0].lower()
        query["sslmode"] = ["disable" if ssl_value in ("false", "disable", "0") else "require"]
    new_query = urlencode(query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, parts.fragment))


def run_migrations_offline():
    """Versión offline"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Versión online"""
    # Obtener URL desde alembic.ini o usar directamente
    url = config.get_main_option("sqlalchemy.url")

    # Si no hay URL en alembic.ini, usar DATABASE_URL síncrona
    if not url:
        from app.core.config import settings
        url = _to_sync_url(settings.DATABASE_URL)

    connectable = create_engine(url, pool_pre_ping=True)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
