# CLAUDE.md — Med-Sync

Reglas de código para el proyecto. Este archivo es autoritativo: cualquier IA o desarrollador que trabaje en este proyecto debe seguirlo sin excepciones.

## Stack

- **Backend**: FastAPI + SQLAlchemy 2.0 (async) + PostgreSQL + Alembic
- **Auth**: python-jose (JWT) + Argon2 (passwords) + slowapi (rate limits)
- **Encryption**: AES-256-GCM vía `EncryptedString` TypeDecorator (`app/db/types.py`)
- **Validation**: Pydantic v2
- **Frontend**: HTML semántico + CSS modular + JS vanilla (sin frameworks)

---

## 🧱 DB Architect

### Herencia de modelos

Todo modelo de datos debe heredar de `TenantBase` o `SystemBase`. Nunca de `Base` directamente.

```python
# CORRECTO
class Patient(TenantBase):
    __tablename__ = "patients"

# INCORRECTO — no usar Base directamente en modelos de negocio
class Patient(Base):
    ...
```

`TenantBase` provee automáticamente: `id` (UUID PK), `clinic_id` (FK → clinics.id), `created_at`, `updated_at`.

`SystemBase` solo se usa para `Clinic` (la raíz del tenant).

### clinic_id en cada query

**Todo query de un modelo TenantBase DEBE incluir `clinic_id` como filtro.** Omitirlo es una fuga de datos cross-tenant.

```python
# CORRECTO
select(Patient).where(
    Patient.clinic_id == ctx.clinic_id,
    Patient.id == patient_id,
)

# INCORRECTO — fuga cross-tenant
select(Patient).where(Patient.id == patient_id)
```

El hook `_enforce_clinic_id` en `base.py` protege en dev, pero el DB FK es la red de seguridad real.

### Relaciones

Todas las relaciones usan `back_populates` explícito. Nunca `backref`. El cascadeo `"all, delete-orphan"` solo en relaciones donde el hijo no tiene sentido sin el padre.

```python
# CORRECTO
appointments: Mapped[List["Appointment"]] = relationship(
    "Appointment",
    back_populates="doctor",
    lazy="select",
    cascade="all, delete-orphan",
    passive_deletes=True,
)
```

Usar `if TYPE_CHECKING:` para imports que solo se necesitan en type hints y evitar circulares.

### Índices

Definir índices en `__table_args__` como tupla. El índice base `ix_{tabla}_clinic_id` lo crea TenantBase. Agregar compuestos para queries frecuentes:

```python
__table_args__ = (
    Index("ix_appts_doctor_starts", "clinic_id", "doctor_id", "starts_at", "ends_at"),
    Index("ix_appts_clinic_starts_status", "clinic_id", "starts_at", "status"),
)
```

Para queries de overlap en appointments: usar el índice `ix_appts_doctor_starts`.

### Restricciones de unicidad

La constraint de exclusión para overlap de citas **vive en la migración Alembic**, no en `__table_args__`, porque SQLAlchemy no soporta `ExcludeConstraint` en la Mapped API:

```sql
ALTER TABLE appointments
ADD CONSTRAINT uq_doctor_no_overlap
EXCLUDE USING GIST (
  doctor_id WITH =,
  tstzrange(starts_at, ends_at, '[)') WITH &&
)
WHERE (status NOT IN ('canceled', 'canceled_by_patient', 'no_show'));
```

### Migraciones Alembic

Cada migración debe tener `upgrade()` y `downgrade()` funcionales. Nunca dejar `downgrade()` vacío o con `pass`.

```python
def upgrade() -> None:
    op.add_column("patients", sa.Column("medical_record_code", sa.String(20), nullable=True))
    op.create_index("ix_patients_medical_record_code", "patients", ["medical_record_code"])

def downgrade() -> None:
    op.drop_index("ix_patients_medical_record_code", table_name="patients")
    op.drop_column("patients", "medical_record_code")
```

Convención de nombres de archivo: `{hash}_{descripcion_corta}.py`.

---

## 🔒 Security Agent

### JWT y refresh tokens

- Access token: 30 minutos (`ACCESS_TOKEN_EXPIRE_MINUTES = 30` en config).
- Refresh token: generado con `secrets.token_urlsafe(48)`, almacenado como hash SHA-256 en la tabla `refresh_tokens`. **Nunca almacenar el token crudo en el DB.**
- El token incluye `sub`, `clinic_id`, `role`, `exp`, `iat`.

```python
# Generar refresh token — patrón en security.py
raw, token_hash, expires_at = generate_refresh_token()
# Enviar `raw` al cliente; guardar `token_hash` en DB
```

### Passwords

Usar siempre Argon2 vía `get_password_hash()` y `verify_password()` de `app/core/security.py`. Nunca bcrypt, nunca SHA-256 para passwords.

### Encriptación de campos sensibles

Campos PII usan `EncryptedString` o `NullableEncryptedString` como TypeDecorator. El sufijo `_enc` en el nombre del campo es obligatorio para campos encriptados.

```python
full_name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
phone: Mapped[Optional[str]] = mapped_column(NullableEncryptedString, nullable=True)
```

Para búsqueda: agregar un campo `_search_hash` con HMAC. **Nunca almacenar plaintext de datos PII para facilitar búsqueda.**

```python
full_name_search_hash: Mapped[str] = mapped_column(String(64), index=True)
# Generado con: make_search_hash(full_name.lower().strip())
```

Campos que **siempre** requieren encriptación: nombre, teléfono, email, fecha de nacimiento, dirección, contacto de emergencia, notas clínicas, tipo de sangre, alergias.

### Rate limits

Configurados con slowapi. Defaults:
- Login / auth: `5/minute` por IP
- Endpoints generales: `100/minute` (default global en `limiter.py`)
- La key function `get_ip_and_clinic` combina IP + clinic_id para mayor granularidad

```python
@router.post("/login")
@limiter.limit("5/minute")
async def login(request: Request, ...):
```

### CORS

- En producción: solo orígenes de `settings.CORS_ORIGINS` (lista separada por comas en `.env.prod`).
- En desarrollo: se agregan los `_DEV_ORIGINS` de `main.py` automáticamente.
- `null` (file://) solo en desarrollo. Nunca en producción.

### Roles y RBAC

Jerarquía: `OWNER` > `DOCTOR` > `ASSISTANT`.

Usar los alias tipados de `deps.py` en los parámetros de ruta:

```python
# CORRECTO
async def create_patient(
    ctx: TenantContext,         # = Depends(get_clinic)
    _: DoctorOrAbove,           # = Depends(require_role(Role.DOCTOR))
):

# Alias disponibles:
# OwnerOnly      → solo OWNER
# DoctorOrAbove  → DOCTOR o OWNER
# AnyStaff       → ASSISTANT, DOCTOR o OWNER
# TenantContext  → clinic validada + db session
```

`ClinicContext` es el contrato de que el endpoint es tenant-safe. Un endpoint que recibe `clinic_id` crudo del request body es una señal de alerta en code review.

### Errores

Nunca exponer detalles internos en respuestas de error. El handler global en `main.py` devuelve siempre `{"detail": "Error interno del servidor. Revisa los logs del backend."}` para excepciones no manejadas. Los logs van al logger, no al cliente.

```python
# CORRECTO
raise HTTPException(status_code=404, detail="Patient not found.")

# INCORRECTO — expone internos
raise HTTPException(status_code=500, detail=str(exc))
```

---

## 🎨 Frontend Agent

### Estructura HTML

HTML5 semántico. Usar `<main>`, `<section>`, `<header>`, `<nav>`, `<aside>`, `<article>` según corresponda. `<div>` solo para contenedores de layout sin semántica propia.

```html
<!-- CORRECTO -->
<main id="main-content">
  <section class="appointments-list">
    <article class="appointment-card" data-id="...">
```

### Fetch centralizado

Todo acceso a la API pasa por el objeto `API` centralizado. Nunca usar `fetch()` directamente en handlers de eventos.

```javascript
// CORRECTO — fetch centralizado con auth headers
const response = await API.get('/appointments');
const response = await API.post('/appointments', body);

// authHeaders() agrega el Bearer token desde localStorage/sessionStorage
function authHeaders() {
    return { 'Authorization': `Bearer ${getToken()}`, 'Content-Type': 'application/json' };
}
```

### Modales

Los modales son reutilizables. Cierre con: clic en overlay, tecla ESC, y botón explícito de cierre.

```javascript
// Abrir
modal.classList.remove('hidden');
document.addEventListener('keydown', closeOnEsc);

// Cerrar — siempre limpiar listeners
function closeModal() {
    modal.classList.add('hidden');
    document.removeEventListener('keydown', closeOnEsc);
}

overlay.addEventListener('click', closeModal);
```

### Diseño responsivo

Mobile-first con CSS custom properties para breakpoints. Nunca estilos inline en JS (excepto para animaciones dinámicas).

```css
/* Base: mobile */
.appointments-grid { display: flex; flex-direction: column; }

/* Tablet+ */
@media (min-width: 768px) {
  .appointments-grid { display: grid; grid-template-columns: repeat(2, 1fr); }
}
```

### Validación de formularios

Validar antes de hacer el fetch. Mostrar errores campo por campo, no solo un mensaje genérico.

```javascript
function validateAppointmentForm(data) {
    const errors = {};
    if (!data.patient_id) errors.patient = 'Selecciona un paciente.';
    if (!data.starts_at)  errors.starts_at = 'La fecha y hora son obligatorias.';
    return errors;
}

const errors = validateAppointmentForm(formData);
if (Object.keys(errors).length > 0) { renderErrors(errors); return; }
```

### Feedback al usuario

Toda acción async debe tener estado de carga (spinner o botón deshabilitado) y feedback de éxito/error visible. Nunca dejar al usuario sin respuesta visual.

```javascript
btn.disabled = true;
btn.textContent = 'Guardando...';
try {
    await API.post('/appointments', body);
    showToast('Cita creada exitosamente.', 'success');
} catch (err) {
    showToast(err.message || 'Error al crear la cita.', 'error');
} finally {
    btn.disabled = false;
    btn.textContent = 'Guardar';
}
```

---

## 📅 Calendar Agent

### Regla de oro

**Nunca permitir dos citas activas en el mismo horario con el mismo doctor.** Esta regla se aplica en dos niveles:

1. **Service layer** (`_assert_no_overlap`): respuesta 409 amigable antes del commit.
2. **DB level**: constraint GIST `uq_doctor_no_overlap` como guard contra race conditions.

Ambos niveles son necesarios. Quitar cualquiera de los dos rompe la garantía.

### `_assert_no_overlap` — patrón obligatorio

```python
async def _assert_no_overlap(
    ctx: ClinicContext,
    doctor_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: Optional[uuid.UUID] = None,   # para moves/updates
) -> None:
    q = select(Appointment).where(
        Appointment.clinic_id == ctx.clinic_id,
        Appointment.doctor_id == doctor_id,
        Appointment.status.in_([s.value for s in ACTIVE_STATUSES]),
        Appointment.starts_at < ends_at,
        Appointment.ends_at   > starts_at,
    )
    if exclude_id:
        q = q.where(Appointment.id != exclude_id)
    conflict = (await ctx.db.execute(q)).scalar_one_or_none()
    if conflict:
        raise HTTPException(status_code=409, detail=f"El doctor ya tiene una cita de {conflict.starts_at} a {conflict.ends_at}.")
```

Llamar `_assert_no_overlap` antes de `db.add()` en **creación** y antes de `flush()` en **reprogramación**.

### Estados válidos de cita

```python
class AppointmentStatus(StrEnum):
    SCHEDULED          = "scheduled"           # activa, ocupa el slot
    COMPLETED          = "completed"           # visita finalizada
    CANCELED           = "canceled"            # cancelada por staff
    CANCELED_BY_PATIENT = "canceled_by_patient" # via magic link
```

- Solo `SCHEDULED` ocupa un slot (`ACTIVE_STATUSES`).
- Las citas canceladas liberan el slot inmediatamente.
- No agregar estados nuevos sin actualizar `ACTIVE_STATUSES` y la constraint GIST.

### Transiciones de estado permitidas

```
SCHEDULED → COMPLETED          (solo staff: DOCTOR o OWNER)
SCHEDULED → CANCELED           (solo staff: ASSISTANT, DOCTOR, OWNER)
SCHEDULED → CANCELED_BY_PATIENT (solo magic link con token válido, ≥1h antes)
```

Rechazar cualquier transición que no esté en esta lista.

### Notas clínicas

Las notas usan el historial de `AppointmentNote`, no un campo de texto único. Nunca sobreescribir; siempre agregar una nota nueva con `created_by_id`.

```python
# CORRECTO
note = AppointmentNote(
    appointment_id=appt.id,
    clinic_id=ctx.clinic_id,
    author_id=current_user.id,
    body_enc=note_text,   # EncryptedString
)
db.add(note)

# INCORRECTO — sobreescribe historial
appt.notes_enc = new_text
```

`notes_enc` en `Appointment` es para notas internas del doctor al crear la cita. El historial clínico va en `AppointmentNote`.

### Validación de horario laboral

Antes de confirmar una cita, verificar que esté dentro de `doctor.working_hours`. El JSONB tiene el esquema:

```json
{
  "mon": [{"start": "09:00", "end": "13:00"}, {"start": "15:00", "end": "19:00"}],
  "tue": [{"start": "09:00", "end": "14:00"}],
  "wed": [],
  ...
}
```

Si `starts_at` cae en un día/hora fuera del horario, devolver 422 con mensaje claro:

```python
raise HTTPException(
    status_code=422,
    detail=f"El doctor no tiene disponibilidad el {day_name} a las {time_str}. "
           f"Su horario ese día es: {schedule_str}."
)
```

### Reprogramación (move)

Al mover una cita:
1. Llamar `_assert_no_overlap(..., exclude_id=appt.id)` para el nuevo horario.
2. Validar que el nuevo horario está dentro de `working_hours`.
3. Actualizar `starts_at` y `ends_at` dentro de una transacción.
4. Solo después hacer `await db.commit()`.

```python
async with db.begin():
    await _assert_no_overlap(ctx, doctor_id, new_start, new_end, exclude_id=appt.id)
    appt.starts_at = new_start
    appt.ends_at   = new_end
    await db.flush()   # dispara la constraint GIST — captura race conditions
```

### Transacciones

Toda operación de escritura en citas usa transacciones explícitas para evitar condiciones de carrera. La constraint GIST en el `flush()` es el último guard antes del `commit()`.

---

## Patrones de código que no deben romperse

### 1. Siempre `from __future__ import annotations`

En todos los archivos de modelos y servicios. Permite referencias forward sin importar el módulo completo.

### 2. Fechas siempre UTC

```python
from datetime import datetime, timezone

# CORRECTO
datetime.now(timezone.utc)

# INCORRECTO — deprecated
datetime.utcnow()
```

Almacenar siempre con `DateTime(timezone=True)` en el modelo. Convertir a la timezone local solo para display, nunca para almacenamiento.

### 3. UUIDs como tipo nativo

```python
import uuid
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ...)
```

Nunca `String(36)` para IDs. El `type_annotation_map` en `Base` mapea `uuid.UUID` → `PG_UUID` automáticamente para columnas `Mapped[uuid.UUID]`.

### 4. Settings via pydantic-settings, nunca `os.getenv` directo

```python
# CORRECTO
from app.core.config import settings
db_url = settings.DATABASE_URL

# INCORRECTO
import os
db_url = os.getenv("DATABASE_URL")
```

### 5. Async consistente

Todos los endpoints y funciones de acceso a DB son `async`. No mezclar sync/async en el mismo path de request.

### 6. Schemas Pydantic v2 para I/O

Los modelos SQLAlchemy no se devuelven directamente al cliente. Siempre pasar por un schema Pydantic con `response_model=`.

```python
@router.get("/patients/{id}", response_model=PatientResponse)
async def get_patient(...):
```

---

## Lo que NO se debe hacer

- **No** agregar `clinic_id` como parámetro de ruta o query string. Viene exclusivamente del JWT via `TenantContext`.
- **No** crear campos PII en plaintext "para facilitar búsqueda". Usar `_search_hash` con HMAC.
- **No** usar `datetime.utcnow()`. Está deprecated; usar `datetime.now(timezone.utc)`.
- **No** omitir `back_populates` en relationships. Hace el ORM imprevisible.
- **No** dejar `downgrade()` vacío en migraciones. Cada migración debe ser reversible.
- **No** hacer `select(Model)` sin filtrar por `clinic_id` en modelos TenantBase.
- **No** exponer stack traces o mensajes de excepción interna al cliente.
- **No** crear estados de cita fuera del FSM definido. Si el negocio requiere uno nuevo, actualizar `ACTIVE_STATUSES` y la constraint GIST.
- **No** sobreescribir `notes_enc` como historial clínico. Agregar `AppointmentNote` entries.
- **No** modificar una cita activa sin revalidar overlap con `_assert_no_overlap`.
- **No** usar `cascade="all, delete-orphan"` en relaciones donde el hijo puede existir sin el padre (e.g. `User → Doctor`: el usuario puede existir sin perfil doctor).
- **No** hardcodear secrets, URLs de producción, ni clinic IDs en el código fuente.
- **No** usar `get_settings()` fuera del módulo `config.py`; importar la instancia `settings` directamente.
- **No** agregar frameworks JS (React, Vue, etc.) al frontend sin discutirlo primero. El proyecto es vanilla JS por decisión arquitectural.

---

## Checklist pre-commit

### 🧱 DB Architect
- [ ] El modelo hereda de `TenantBase` (o `SystemBase` si es raíz de tenant).
- [ ] Todo query nuevo filtra por `clinic_id`.
- [ ] Relaciones tienen `back_populates` y `lazy="select"`.
- [ ] Nuevos índices compuestos agregados en `__table_args__`.
- [ ] La migración Alembic tiene `upgrade()` y `downgrade()` funcionales.
- [ ] No hay columnas PII en plaintext sin `EncryptedString`.

### 🔒 Security Agent
- [ ] Endpoints nuevos tienen `TenantContext` y el rol mínimo requerido (`OwnerOnly`, `DoctorOrAbove`, `AnyStaff`).
- [ ] Rate limit explícito en endpoints de auth (`5/minute`).
- [ ] Errores no exponen detalles de excepción ni stack traces.
- [ ] Refresh tokens almacenados como hash SHA-256, nunca en crudo.
- [ ] CORS no ampliado más allá de lo necesario.
- [ ] Secrets nuevos en `.env.example` con placeholder `CHANGE_ME`.

### 🎨 Frontend Agent
- [ ] Fetch pasa por `API` centralizado, no `fetch()` directo.
- [ ] Formularios validados antes de enviar.
- [ ] Feedback de carga y resultado visible para el usuario.
- [ ] Modales cierran con overlay, ESC, y botón explícito.
- [ ] Sin estilos inline en JS (excepto animaciones dinámicas).
- [ ] Sin `console.log` de datos sensibles (tokens, PII).

### 📅 Calendar Agent
- [ ] `_assert_no_overlap` llamado antes de crear o mover una cita.
- [ ] Estado nuevo de cita? → actualizar `ACTIVE_STATUSES` y constraint GIST.
- [ ] Notas clínicas agregadas como `AppointmentNote`, no sobreescritas.
- [ ] Horario laboral del doctor validado antes de confirmar la cita.
- [ ] Reprogramación ocurre dentro de una transacción con `flush()` antes del `commit()`.
- [ ] Cita cancelada → slot liberado inmediatamente (status fuera de `ACTIVE_STATUSES`).
