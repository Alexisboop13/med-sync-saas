"""
app/agent/scheduler.py
──────────────────────────────────────────────────────────────────────────────
Lógica central del agente: descubre qué acciones están pendientes y las ejecuta.

Diseño sin estado:
  Cada ejecución de `run_all_tasks()` consulta el DB y actúa.
  El historial está en la tabla `notifications` — si existe un registro
  SENT de tipo APPOINTMENT_REMINDER para la cita, no se re-envía.

Ventana de recordatorio: 30–120 minutos antes del inicio.
  - Si la cita empieza en ≤ 2 horas y no tiene reminder enviado → enviar.
  - Si ya pasó la cita y el estado sigue SCHEDULED → marcar como no-show
    automáticamente (opcional; desactivado por defecto).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.appointment import Appointment, AppointmentStatus
from app.models.clinic import Clinic
from app.models.doctor import Doctor
from app.models.notification import Notification, NotificationStatus, NotificationType
from app.models.patient import Patient
from app.models.user import User

log = logging.getLogger(__name__)

# Citas que empiezan entre REMINDER_MIN y REMINDER_MAX minutos en el futuro
REMINDER_MIN_MINUTES = 30
REMINDER_MAX_MINUTES = 120

# Si True, marca automáticamente como no-show las citas que ya pasaron
AUTO_NOSHOW_ENABLED = False
AUTO_NOSHOW_GRACE_MINUTES = 30   # espera N min después de ends_at antes de marcar


@dataclass
class ReminderTask:
    appointment_id: uuid.UUID
    clinic_id: uuid.UUID
    patient_name: str
    patient_email: str | None
    patient_phone: str | None
    doctor_name: str
    clinic_name: str
    starts_at: datetime
    ends_at: datetime
    magic_token: str | None
    reason: str | None


@dataclass
class AgentRunResult:
    reminders_sent: int
    reminders_failed: int
    noshows_marked: int
    errors: List[str]
    ran_at: datetime


async def _already_reminded(db: AsyncSession, appointment_id: uuid.UUID) -> bool:
    """Devuelve True si ya existe una notificación de reminder SENT para esta cita."""
    result = await db.execute(
        select(Notification.id).where(
            Notification.appointment_id == appointment_id,
            Notification.notification_type == NotificationType.APPOINTMENT_REMINDER,
            Notification.status == NotificationStatus.SENT,
        )
    )
    return result.scalar_one_or_none() is not None


async def _get_pending_reminders(db: AsyncSession) -> List[ReminderTask]:
    """
    Encuentra citas SCHEDULED en la ventana de recordatorio que no han recibido
    un reminder. Hace un solo JOIN para minimizar queries.
    """
    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=REMINDER_MIN_MINUTES)
    window_end = now + timedelta(minutes=REMINDER_MAX_MINUTES)

    result = await db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
            selectinload(Appointment.clinic),
        )
        .where(
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.starts_at >= window_start,
            Appointment.starts_at <= window_end,
        )
    )
    appointments = result.scalars().all()

    tasks: List[ReminderTask] = []
    for appt in appointments:
        if await _already_reminded(db, appt.id):
            continue

        patient = appt.patient
        doctor = appt.doctor
        clinic = appt.clinic
        doc_user: User | None = doctor.user if doctor else None

        patient_name = _safe_decrypt(patient.full_name_enc if patient else None, "Paciente")
        patient_email = _safe_decrypt(patient.email_enc if patient else None, None)
        patient_phone = _safe_decrypt(patient.phone_enc if patient else None, None)
        doctor_name = _safe_decrypt(doc_user.full_name_enc if doc_user else None, "Doctor")
        full_doctor = f"{doctor.title or 'Dr.'} {doctor_name}".strip() if doctor else doctor_name

        tasks.append(ReminderTask(
            appointment_id=appt.id,
            clinic_id=appt.clinic_id,
            patient_name=patient_name,
            patient_email=patient_email,
            patient_phone=patient_phone,
            doctor_name=full_doctor,
            clinic_name=clinic.name if clinic else "MedSync",
            starts_at=appt.starts_at,
            ends_at=appt.ends_at,
            magic_token=appt.magic_token,
            reason=appt.reason,
        ))

    return tasks


async def _get_auto_noshow_candidates(db: AsyncSession) -> List[Appointment]:
    """Citas SCHEDULED que ya deberían haber terminado (grace period superado)."""
    if not AUTO_NOSHOW_ENABLED:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=AUTO_NOSHOW_GRACE_MINUTES)
    result = await db.execute(
        select(Appointment).where(
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.ends_at < cutoff,
        )
    )
    return result.scalars().all()


async def run_all_tasks(db: AsyncSession) -> AgentRunResult:
    """
    Punto de entrada principal del agente. Llámalo desde el scheduler o
    desde el endpoint /internal/agent/run.
    """
    from app.agent.notifier import send_reminder  # evita circular import

    ran_at = datetime.now(timezone.utc)
    reminders_sent = 0
    reminders_failed = 0
    noshows_marked = 0
    errors: List[str] = []

    # ── 1. Recordatorios de citas próximas ─────────────────────────────────────
    try:
        pending = await _get_pending_reminders(db)
        log.info("Agente: %d citas necesitan recordatorio.", len(pending))

        for task in pending:
            try:
                ok = await send_reminder(db, task)
                if ok:
                    reminders_sent += 1
                else:
                    reminders_failed += 1
            except Exception as exc:
                msg = f"Error en reminder para cita {task.appointment_id}: {exc}"
                log.error(msg, exc_info=True)
                errors.append(msg)
                reminders_failed += 1
    except Exception as exc:
        msg = f"Error consultando recordatorios pendientes: {exc}"
        log.error(msg, exc_info=True)
        errors.append(msg)

    # ── 2. Auto no-show (desactivado por defecto) ──────────────────────────────
    if AUTO_NOSHOW_ENABLED:
        try:
            candidates = await _get_auto_noshow_candidates(db)
            for appt in candidates:
                appt.status = AppointmentStatus.NO_SHOW
                appt.was_no_show = True
                noshows_marked += 1
            if candidates:
                await db.commit()
                log.info("Agente: %d no-shows marcados automáticamente.", noshows_marked)
        except Exception as exc:
            msg = f"Error marcando no-shows: {exc}"
            log.error(msg, exc_info=True)
            errors.append(msg)

    return AgentRunResult(
        reminders_sent=reminders_sent,
        reminders_failed=reminders_failed,
        noshows_marked=noshows_marked,
        errors=errors,
        ran_at=ran_at,
    )


async def get_agent_status(db: AsyncSession) -> dict:
    """Datos para el dashboard del agente (próximas acciones + actividad reciente)."""
    now = datetime.now(timezone.utc)

    # Citas en las próximas 3 horas (para mostrar en dashboard)
    upcoming_result = await db.execute(
        select(Appointment)
        .options(
            selectinload(Appointment.patient),
            selectinload(Appointment.doctor).selectinload(Doctor.user),
        )
        .where(
            Appointment.status == AppointmentStatus.SCHEDULED,
            Appointment.starts_at >= now,
            Appointment.starts_at <= now + timedelta(hours=3),
        )
        .order_by(Appointment.starts_at)
        .limit(20)
    )
    upcoming = upcoming_result.scalars().all()

    upcoming_items = []
    for appt in upcoming:
        reminded = await _already_reminded(db, appt.id)
        patient = appt.patient
        doctor = appt.doctor
        doc_user = doctor.user if doctor else None

        patient_name = _safe_decrypt(patient.full_name_enc if patient else None, "—")
        doctor_name = _safe_decrypt(doc_user.full_name_enc if doc_user else None, "—")
        minutes_away = int((appt.starts_at - now).total_seconds() / 60)

        upcoming_items.append({
            "appointment_id": str(appt.id),
            "patient_name": patient_name,
            "doctor_name": f"{doctor.title or 'Dr.'} {doctor_name}".strip() if doctor else doctor_name,
            "starts_at": appt.starts_at.isoformat(),
            "minutes_away": minutes_away,
            "reminder_sent": reminded,
            "patient_confirmed": appt.patient_confirmed_at is not None,
        })

    # Notificaciones recientes (últimas 24h)
    recent_result = await db.execute(
        select(Notification)
        .where(
            Notification.notification_type == NotificationType.APPOINTMENT_REMINDER,
            Notification.created_at >= now - timedelta(hours=24),
        )
        .order_by(Notification.created_at.desc())
        .limit(50)
    )
    recent = recent_result.scalars().all()

    recent_items = [
        {
            "id": str(n.id),
            "channel": n.channel,
            "status": n.status,
            "sent_at": n.sent_at.isoformat() if n.sent_at else None,
            "created_at": n.created_at.isoformat(),
            "appointment_id": str(n.appointment_id) if n.appointment_id else None,
        }
        for n in recent
    ]

    return {
        "upcoming_appointments": upcoming_items,
        "recent_notifications": recent_items,
        "checked_at": now.isoformat(),
        "reminder_window_minutes": [REMINDER_MIN_MINUTES, REMINDER_MAX_MINUTES],
    }


def _safe_decrypt(value: str | None, fallback) -> str | None:
    if value is None:
        return fallback
    try:
        from app.core.crypto import decrypt  # noqa: PLC0415
        return decrypt(value)
    except Exception:
        return value
