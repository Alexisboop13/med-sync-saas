# Med-Sync — SaaS de gestión de citas médicas

**Med-Sync** es un backend REST multi-tenant para clínicas (dental/médica). Gestiona pacientes, citas sin conflictos de horario, y da a los pacientes autoservicio (cancelar/confirmar/reagendar via magic link) sin necesidad de una cuenta propia.

Proyecto de portafolio construido durante una transición de Data Analyst a Ingeniería de Software. Énfasis en seguridad de datos clínicos: cifrado AES-256-GCM en PII, auditoría tipo NOM-024, multi-tenancy por JWT.

---

## Stack tecnológico

| Capa | Tecnología |
|------|-----------|
| Framework | FastAPI 0.136 |
| ORM | SQLAlchemy 2.0 async |
| Base de datos | PostgreSQL 16 |
| Migraciones | Alembic |
| Auth | PyJWT + Argon2-cffi |
| Cifrado PII | AES-256-GCM (`cryptography`) |
| Validación I/O | Pydantic v2 |
| Rate limiting | slowapi + Redis |
| Archivos médicos | AWS S3 (boto3) |
| Pagos | Stripe |
| Email | SMTP nativo |
| WhatsApp | GreenAPI |
| Scheduler | APScheduler |
| Servidor | Uvicorn / Gunicorn |
| Frontend | HTML5 + CSS modular + Vanilla JS |

---

## Requisitos

- Python 3.12+
- Docker (para Postgres + Redis en local)
- PostgreSQL 16 con la extensión `btree_gist` habilitada (para el constraint de overlap de citas)
- Redis — opcional con un solo worker; **requerido** en staging/prod

---

## Instalación local

```bash
# 1. Clonar e instalar dependencias
git clone https://github.com/Alexisboop13/med-sync.git
cd med-sync
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Levantar Postgres 16 y Redis 7 con Docker
docker compose up -d

# 3. Configurar variables de entorno
cp .env.example .env.dev
# Editar .env.dev: DATABASE_URL, JWT_SECRET_KEY, ENCRYPTION_KEYS, SEARCH_HMAC_KEY

# 4. Aplicar migraciones
APP_ENV=development alembic upgrade head

# 5. Levantar el servidor
APP_ENV=development uvicorn app.main:app --reload --workers 1
```

El servidor queda disponible en `http://localhost:8000`.  
Swagger UI: `http://localhost:8000/docs` (solo en development; deshabilitado en production).

> **Nota:** el `docker-compose.yml` levanta **solo la infraestructura** (Postgres + Redis). La app corre fuera del compose para que `--reload` funcione correctamente.

---

## Variables de entorno

Todas las variables están documentadas en `.env.example`. Las críticas para dev:

| Variable | Cómo generarla |
|---------|----------------|
| `JWT_SECRET_KEY` | `openssl rand -hex 32` |
| `ENCRYPTION_KEYS` | `python -c "from app.core.crypto import generate_key_b64; print(generate_key_b64())"` |
| `SEARCH_HMAC_KEY` | `openssl rand -hex 32` |
| `AGENT_SECRET_KEY` | `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DATABASE_URL` | `postgresql+asyncpg://medsync:medsync_dev@localhost:5432/medsync_db` (valores del docker-compose) |
| `REDIS_URL` | `redis://localhost:6379` (con Docker); vacío si se usa 1 solo worker |

Los servicios externos (SMTP, S3, Stripe, WhatsApp/GreenAPI) pueden dejarse vacíos en desarrollo: las funcionalidades que dependen de ellos fallan silenciosamente o lanzan errores descriptivos.

---

## API — Referencia de endpoints

> La clasificación ✅ / ⚠️ está basada en **revisión estática del código**, no en pruebas runtime sistemáticas de cada endpoint. ✅ significa que la lógica está completa e implementada correctamente según el código. ⚠️ significa que el código existe pero la funcionalidad depende de un servicio externo que puede no estar configurado.

### Auth — público

| Método | Ruta | Descripción | Estado |
|--------|------|-------------|--------|
| POST | `/api/v1/auth/register` | Registrar clínica nueva o usuario en clínica existente | ✅ |
| POST | `/api/v1/auth/login` | Login con email + password | ✅ |
| POST | `/api/v1/auth/refresh-token` | Renovar access token (JWT 30 min) | ✅ |
| POST | `/api/v1/auth/logout` | Revocar refresh token | ✅ |
| POST | `/api/v1/auth/forgot-password` | Solicitar reset de contraseña por email | ⚠️ Requiere SMTP |
| POST | `/api/v1/auth/reset-password` | Confirmar nuevo password con token | ✅ |

### Pacientes — requiere JWT

| Método | Ruta | Descripción | Estado |
|--------|------|-------------|--------|
| POST | `/api/v1/patients` | Crear paciente (PII cifrada, código de expediente único) | ✅ |
| GET | `/api/v1/patients` | Listar pacientes (paginado) | ✅ |
| GET | `/api/v1/patients/search?q=` | Buscar por nombre, email o teléfono | ✅ |
| GET | `/api/v1/patients/by-code/{code}` | Buscar por código de expediente | ✅ |
| GET | `/api/v1/patients/{id}` | Obtener paciente | ✅ |
| PUT | `/api/v1/patients/{id}` | Actualizar (mantiene cifrado) | ✅ |
| DELETE | `/api/v1/patients/{id}` | Soft-delete | ✅ |
| GET | `/api/v1/patients/{id}/timeline` | Historial cronológico de citas y PDFs | ✅ |
| GET | `/api/v1/patients/{id}/can-book` | Verificar si está bloqueado por inasistencias | ✅ |
| POST | `/api/v1/patients/{src_id}/merge/{tgt_id}` | Fusionar expedientes duplicados (solo OWNER) | ✅ |
| POST | `/api/v1/patients/{id}/regenerate-code` | Regenerar código de expediente (solo OWNER) | ✅ |

### Citas — requiere JWT

| Método | Ruta | Descripción | Estado |
|--------|------|-------------|--------|
| POST | `/api/v1/appointments` | Crear cita (valida overlap doble capa) | ✅ |
| GET | `/api/v1/appointments` | Listar (paginado, filtros por doctor/paciente/fecha/estado) | ✅ |
| GET | `/api/v1/appointments/agenda?fecha=YYYY-MM-DD` | Agenda del día ordenada | ✅ |
| GET | `/api/v1/appointments/range` | Citas en rango de fechas (máx. 90 días) | ✅ |
| GET | `/api/v1/appointments/export?format=csv\|xlsx` | Exportar a CSV o Excel | ✅ |
| GET | `/api/v1/appointments/export/ics` | Exportar a iCalendar (.ics) RFC 5545 | ✅ |
| GET | `/api/v1/appointments/{id}` | Obtener cita | ✅ |
| PUT | `/api/v1/appointments/{id}` | Editar cita (revalida overlap) | ✅ |
| DELETE | `/api/v1/appointments/{id}` | Cancelar cita (libera slot) | ✅ |
| PATCH | `/api/v1/appointments/{id}/status` | Transición de estado (completar/cancelar) | ✅ |
| PATCH | `/api/v1/appointments/{id}/mark-no-show` | Marcar como no-show + incrementar contador | ✅ |
| GET | `/api/v1/appointments/{id}/notes` | Listar notas clínicas del historial | ✅ |
| POST | `/api/v1/appointments/{id}/notes` | Agregar nota clínica (inmutable, nunca sobreescribe) | ✅ |
| DELETE | `/api/v1/appointments/{id}/notes/{nid}` | Eliminar nota (solo autor o OWNER) | ✅ |
| POST | `/api/v1/appointments/{id}/propose-reschedule` | Proponer nueva fecha al paciente por email | ⚠️ Requiere SMTP |
| POST | `/api/v1/appointments/{id}/attach-pdf` | Adjuntar PDF a la cita (máx. 10 MB) | ⚠️ Requiere S3 |

### Autoservicio del paciente — público (magic link)

| Método | Ruta | Descripción | Estado |
|--------|------|-------------|--------|
| GET | `/api/v1/appointments/public/slots` | Ver slots disponibles de un doctor | ✅ |
| GET | `/api/v1/appointments/public/{token}` | Ver detalle de la propia cita | ✅ |
| PUT | `/api/v1/appointments/public/{token}` | Cancelar cita (mín. 1h de anticipación) | ✅ |
| POST | `/api/v1/appointments/public/confirm/{token}` | Confirmar asistencia | ✅ |
| POST | `/api/v1/appointments/public/reschedule-request/{token}` | Solicitar cambio de fecha | ⚠️ Requiere SMTP |
| GET | `/api/v1/appointments/public/reschedule/{token}/confirm` | Confirmar propuesta de fecha del staff | ✅ |
| GET | `/api/v1/appointments/public/reschedule/{token}/reject` | Rechazar propuesta, mantiene fecha original | ✅ |

### Otros módulos autenticados

| Módulo | Ruta base | Estado |
|--------|-----------|--------|
| Doctores | `/api/v1/doctors` | ✅ |
| Ubicaciones / sucursales | `/api/v1/locations` | ✅ |
| Usuarios del staff | `/api/v1/users` | ✅ |
| Configuración de clínica | `/api/v1/clinics` | ✅ |
| Registros médicos | `/api/v1/medical-records` | ⚠️ Requiere S3 para PDFs |
| Analytics / dashboard | `/api/v1/analytics` | ✅ |
| Facturación (Stripe) | `/api/v1/billing` | ⚠️ Código completo, no probado en producción |
| Webhooks (Stripe) | `/api/v1/webhooks` | ⚠️ Requiere Stripe configurado |
| Health checks | `/health`, `/health/db` | ✅ |
| Agente interno | `/internal/agent/run`, `/internal/agent/status` | ⚠️ Requiere `AGENT_SECRET_KEY` |

---

## Arquitectura y decisiones de seguridad

**Multi-tenancy por JWT:**  
Cada request autenticado lleva `clinic_id` en el token. Todo query a la base de datos filtra por `clinic_id` proveniente del JWT, nunca del body del request. No hay datos compartidos entre clínicas.

**Cifrado de PII en reposo:**  
Nombre, teléfono, email, fecha de nacimiento, dirección, notas clínicas y contacto de emergencia se almacenan cifrados con AES-256-GCM vía el TypeDecorator `EncryptedString`. La búsqueda usa hashes HMAC sin exponer texto plano en la base de datos.

**Passwords:**  
Argon2 exclusivamente. Sin bcrypt, sin SHA-256 para contraseñas.

**Refresh tokens:**  
Almacenados como hash SHA-256 en la tabla `refresh_tokens`. El token crudo se envía al cliente una única vez y nunca se persiste en texto plano.

**Overlap de citas — doble capa:**  
1. Service layer: `_assert_no_overlap()` antes de insertar → respuesta 409 legible con el horario en conflicto.  
2. DB level: constraint GIST de exclusión sobre `tstzrange(starts_at, ends_at)` para capturar race conditions concurrentes.

**Auditoría:**  
Tabla `audit_logs` con eventos de login, CRUD de pacientes, creación/cancelación/no-show de citas. Compatible con los requerimientos de trazabilidad de NOM-024 (México).

---

## Estructura del proyecto

```
med-sync/
├── app/
│   ├── main.py                    # Inicialización FastAPI, routers, startup/shutdown
│   ├── api/
│   │   ├── deps.py                # Dependencias: JWT, roles (OwnerOnly/DoctorOrAbove/AnyStaff), TenantContext
│   │   └── routes/                # Un módulo por recurso (appointments, patients, auth…)
│   ├── core/
│   │   ├── config.py              # Settings con pydantic-settings (APP_ENV aware)
│   │   ├── security.py            # JWT, Argon2, generación de refresh tokens
│   │   ├── crypto.py              # AES-256-GCM, hashes HMAC para búsqueda
│   │   ├── email.py               # Envío SMTP (confirmaciones, password reset, propuestas)
│   │   ├── s3.py                  # Upload/presigned URLs en S3
│   │   ├── whatsapp.py            # Integración GreenAPI
│   │   └── limiter.py             # slowapi + Redis
│   ├── db/
│   │   ├── session.py             # Engine async + AsyncSession factory
│   │   └── types.py               # EncryptedString / NullableEncryptedString TypeDecorators
│   ├── models/                    # Modelos SQLAlchemy (todos heredan de TenantBase o SystemBase)
│   ├── schemas/                   # Schemas Pydantic v2 para I/O (nunca se exponen los modelos ORM directamente)
│   ├── services/
│   │   └── audit.py               # log_audit() — registro de eventos de auditoría
│   └── agent/                     # APScheduler para recordatorios automáticos de citas
├── migrations/
│   └── versions/                  # Migraciones Alembic (todas con upgrade + downgrade funcionales)
├── scripts/
│   ├── generate_secrets.py        # Helper para generar ENCRYPTION_KEYS y JWT_SECRET_KEY
│   ├── encrypt_existing_patients.py
│   └── migrate_pdfs_to_s3.py
├── docker-compose.yml             # Postgres 16 + Redis 7 para desarrollo local
├── Dockerfile
├── Procfile                       # uvicorn app.main:app para Render
├── .env.example                   # Plantilla completa de variables (sin valores reales)
└── requirements.txt
```

---

## Despliegue en producción (Render)

```bash
# Procfile (incluido)
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

En `ENVIRONMENT=production`:
- `/docs`, `/redoc` y `/openapi.json` quedan deshabilitados automáticamente.
- CORS solo permite los orígenes configurados en `CORS_ORIGINS`.
- Redis es requerido para que el rate limiting funcione correctamente con múltiples workers.

---

## Roadmap / Próximos pasos

- [ ] **Suite de tests** — es la deuda técnica más importante. No hay tests automatizados; la cobertura actual es 0%.
- [ ] **Validar flujo de email end-to-end** — confirmación de cita, propuesta de reagendamiento y reset de contraseña con SMTP de producción (SES / Resend).
- [ ] **Validar billing Stripe** — checkout session, webhooks y ciclo de vida de suscripciones en modo prueba.
- [ ] **Validar WhatsApp** — integración GreenAPI end-to-end con número real.
- [ ] **Rotación de claves de cifrado** — script para re-cifrar PII cuando se agrega una nueva clave a `ENCRYPTION_KEYS`.
- [ ] **Segundo factor de autenticación** — TOTP o magic-link para acceso de staff en producción.
- [ ] **Frontend más completo** — el `index.html` actual cubre el flujo principal; el módulo de billing y el panel de super-admin están pendientes.
- [ ] **Documentación interna de la API** — completar Swagger descriptions por endpoint.

---

## Autor

**Alexis Dehesa**  
Transición de Data Analyst a Ingeniería de Software.  
GitHub: [@Alexisboop13](https://github.com/Alexisboop13)
