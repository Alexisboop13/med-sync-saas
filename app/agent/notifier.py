"""
app/agent/notifier.py
──────────────────────────────────────────────────────────────────────────────
Envío de recordatorios y registro del intento en la tabla `notifications`.

Flujo:
  1. Construye el mensaje (email HTML + texto plano).
  2. Intenta enviar por SMTP.
  3. Si tiene teléfono y GreenAPI está configurado, también envía WhatsApp.
  4. Registra el resultado en la tabla `notifications` (audit trail).
  5. Devuelve True si al menos un canal tuvo éxito.
"""

from __future__ import annotations
from app.models.notification import Notification, NotificationChannel, NotificationStatus, NotificationType
from app.core.whatsapp import format_appointment_reminder, send_whatsapp
from app.core.config import settings
from app.agent.scheduler import ReminderTask
from sqlalchemy.ext.asyncio import AsyncSession
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
import ssl
import smtplib
import logging
logger = logging.getLogger(__name__)


log = logging.getLogger(__name__)

_ES_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)

_REMINDER_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Recordatorio de Cita</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#0891b2,#0e7490);padding:32px 40px;text-align:center;">
          <div style="font-size:40px;margin-bottom:8px;">⏰</div>
          <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700;">{clinic_name}</h1>
          <p style="color:#cffafe;margin:6px 0 0;font-size:14px;">Recordatorio de Cita</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 16px;">
            Hola, <strong>{patient_name}</strong>,
          </p>
          <p style="color:#475569;font-size:15px;margin:0 0 24px;line-height:1.6;">
            Tu cita es <strong style="color:#0891b2;">en aproximadamente {minutes_away} minutos</strong>.
          </p>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f0f9ff;border-radius:8px;padding:0 24px;margin-bottom:28px;border:1px solid #e0f2fe;">
            <tr><td style="padding:16px 0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Doctor</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{doctor_name}</span>
            </td></tr>
            <tr><td style="padding:16px 0;border-top:1px solid #e0f2fe;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Fecha y hora</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{date_str} a las {time_str}</span>
            </td></tr>
            {reason_row}
          </table>
          {magic_link_section}
          <p style="color:#94a3b8;font-size:12px;line-height:1.6;margin:0;">
            Mensaje autom&aacute;tico de {clinic_name} &mdash; no responder.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

_MAGIC_LINK_SECTION = """\
          <p style="color:#475569;font-size:14px;margin:0 0 20px;line-height:1.6;">
            ¿Necesitas cancelar o reagendar? Usa tu enlace personal:
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
            <tr><td style="text-align:center;">
              <a href="{magic_link}"
                 style="display:inline-block;background:#0891b2;color:#fff;text-align:center;
                        padding:12px 28px;border-radius:8px;font-size:14px;font-weight:600;
                        text-decoration:none;">
                Gestionar mi cita
              </a>
            </td></tr>
          </table>"""

_REMINDER_TEXT = """\
Recordatorio de Cita — {clinic_name}
======================================

Hola, {patient_name}.

Tu cita es en aproximadamente {minutes_away} minutos.

  Doctor : {doctor_name}
  Fecha  : {date_str} a las {time_str}
{reason_line}
{magic_link_line}
— {clinic_name} (mensaje automático, no responder)
"""


def _build_reminder_email(task: ReminderTask, minutes_away: int) -> MIMEMultipart:
    date_str = (
        f"{task.starts_at.day} de "
        f"{_ES_MONTHS[task.starts_at.month - 1]} de "
        f"{task.starts_at.year}"
    )
    h = task.starts_at.hour % 12 or 12
    period = "AM" if task.starts_at.hour < 12 else "PM"
    time_str = f"{h}:{task.starts_at.minute:02d} {period}"

    reason_row = (
        f"<tr><td style='padding:16px 0;border-top:1px solid #e0f2fe;'>"
        f"<span style='color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;'>Motivo</span><br>"
        f"<span style='color:#1e293b;font-size:15px;'>{task.reason}</span>"
        f"</td></tr>"
    ) if task.reason else ""

    magic_url = (
        f"{settings.APP_BASE_URL}/appointments/public/{task.magic_token}"
        if task.magic_token else None
    )
    magic_link_section = _MAGIC_LINK_SECTION.format(
        magic_link=magic_url) if magic_url else ""
    magic_link_line = f"\nGestionar mi cita: {magic_url}\n" if magic_url else ""
    reason_line = f"  Motivo : {task.reason}\n" if task.reason else ""

    html = _REMINDER_HTML.format(
        clinic_name=task.clinic_name,
        patient_name=task.patient_name,
        minutes_away=minutes_away,
        doctor_name=task.doctor_name,
        date_str=date_str,
        time_str=time_str,
        reason_row=reason_row,
        magic_link_section=magic_link_section,
    )
    text = _REMINDER_TEXT.format(
        clinic_name=task.clinic_name,
        patient_name=task.patient_name,
        minutes_away=minutes_away,
        doctor_name=task.doctor_name,
        date_str=date_str,
        time_str=time_str,
        reason_line=reason_line,
        magic_link_line=magic_link_line,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"⏰ Recordatorio: tu cita es en {minutes_away} min — {task.clinic_name}"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = task.patient_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _send_email_sync(msg: MIMEMultipart, to_email: str) -> bool:
    try:
        import requests
        api_key = settings.SENDGRID_API_KEY
        from_email = settings.EMAILS_FROM

        data = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": from_email},
            "subject": msg['Subject'],
            "content": [{"type": "text/html", "value": msg.get_payload()}]
        }

        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {api_key}"},
            json=data
        )
        return response.status_code == 202
    except Exception as e:
        logger.error(f"SendGrid error: {e}")
        return False


async def send_reminder(db: AsyncSession, task: ReminderTask) -> bool:
    """
    Envía recordatorio por email y/o WhatsApp, luego registra en `notifications`.
    Devuelve True si al menos un canal tuvo éxito.
    """
    now = datetime.now(timezone.utc)
    minutes_away = max(1, int((task.starts_at - now).total_seconds() / 60))

    email_ok = False
    whatsapp_ok = False

    # ── Email ──────────────────────────────────────────────────────────────────
    if task.patient_email:
        msg = _build_reminder_email(task, minutes_away)
        import asyncio  # noqa: PLC0415
        loop = asyncio.get_event_loop()
        email_ok = await loop.run_in_executor(None, _send_email_sync, msg, task.patient_email)

    # ── WhatsApp ───────────────────────────────────────────────────────────────
    if task.patient_phone and settings.GREENAPI_INSTANCE_ID:
        magic_url = (
            f"{settings.APP_BASE_URL}/appointments/public/{task.magic_token}"
            if task.magic_token else None
        )
        wa_msg = format_appointment_reminder(
            patient_name=task.patient_name,
            doctor_name=task.doctor_name,
            clinic_name=task.clinic_name,
            starts_at_local=task.starts_at.strftime("%d/%m/%Y %H:%M"),
            magic_link=magic_url or "",
        )
        whatsapp_ok = await send_whatsapp(task.patient_phone, wa_msg)

    success = email_ok or whatsapp_ok

    # ── Registrar en notifications (audit trail) ───────────────────────────────
    try:
        notif = Notification(
            clinic_id=task.clinic_id,
            appointment_id=task.appointment_id,
            channel=NotificationChannel.EMAIL if email_ok else NotificationChannel.WHATSAPP,
            notification_type=NotificationType.APPOINTMENT_REMINDER,
            status=NotificationStatus.SENT if success else NotificationStatus.FAILED,
            sent_at=now if success else None,
            attempt_count=1,
            extra_data={
                "email_ok": email_ok,
                "whatsapp_ok": whatsapp_ok,
                "minutes_away": minutes_away,
            },
        )
        db.add(notif)
        await db.commit()
    except Exception:
        log.error("Error guardando registro de notificación", exc_info=True)
        await db.rollback()

    return success
