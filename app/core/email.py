"""
app/core/email.py
──────────────────────────────────────────────────────────────────────────────
Appointment confirmation emails via SMTP.

Sends via BackgroundTasks (runs in FastAPI's threadpool) so the endpoint
returns 201 immediately. If SMTP is unconfigured or the send fails, errors
are logged but never bubble up to the caller.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from fastapi import BackgroundTasks

from app.core.config import settings

log = logging.getLogger(__name__)

_ES_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fmt_time(dt: datetime) -> str:
    period = "AM" if dt.hour < 12 else "PM"
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {period}"


_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Confirmación de Cita</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#2563eb,#1d4ed8);padding:32px 40px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-.5px;">MedSync</h1>
          <p style="color:#bfdbfe;margin:8px 0 0;font-size:14px;">Confirmación de Cita Médica</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 20px;">
            Hola, <strong>{patient_name}</strong>,
          </p>
          <p style="color:#475569;font-size:15px;margin:0 0 28px;line-height:1.6;">
            Tu cita ha sido <strong style="color:#16a34a;">confirmada exitosamente</strong>.
            Aquí tienes el resumen:
          </p>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f1f5f9;border-radius:8px;padding:0 24px;margin-bottom:28px;">
            <tr><td style="padding:16px 0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Doctor</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">Dr. {doctor_name}</span>
            </td></tr>
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Fecha</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{date_str}</span>
            </td></tr>
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Horario</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{starts_time} &ndash; {ends_time}</span>
            </td></tr>
            {reason_row}
          </table>
          <p style="color:#64748b;font-size:13px;line-height:1.6;margin:0;">
            Si necesitas cancelar o reprogramar tu cita, comunícate con la clínica
            con al menos 24&nbsp;horas de anticipación.
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #e2e8f0;">
          <p style="color:#94a3b8;font-size:12px;margin:0;">
            Mensaje automático de MedSync &mdash; no responder a este correo.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>
"""

_REASON_ROW = """\
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Motivo</span><br>
              <span style="color:#1e293b;font-size:15px;">{reason}</span>
            </td></tr>"""

_TEXT = """\
Confirmación de Cita — MedSync
================================

Hola, {patient_name}.

Tu cita ha sido confirmada.

  Doctor  : Dr. {doctor_name}
  Fecha   : {date_str}
  Horario : {starts_time} – {ends_time}{reason_line}

Si necesitas cancelar o reprogramar, comunícate con la clínica
con al menos 24 horas de anticipación.

— MedSync (mensaje automático, no responder)
"""


def _build_mime(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None,
) -> MIMEMultipart:
    date_str = (
        f"{starts_at.day} de {_ES_MONTHS[starts_at.month - 1]} de {starts_at.year}"
    )
    starts_time = _fmt_time(starts_at)
    ends_time = _fmt_time(ends_at)
    reason_row = _REASON_ROW.format(reason=reason) if reason else ""
    reason_line = f"\n  Motivo  : {reason}" if reason else ""

    html = _HTML.format(
        patient_name=patient_name,
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        ends_time=ends_time,
        reason_row=reason_row,
    )
    text = _TEXT.format(
        patient_name=patient_name,
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        ends_time=ends_time,
        reason_line=reason_line,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Confirmación de cita — Dr. {doctor_name}"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = patient_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _send_sync(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None,
) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        log.warning(
            "SMTP not configured (SMTP_HOST or SMTP_USER empty) — skipping confirmation email."
        )
        return

    msg = _build_mime(patient_email, patient_name, doctor_name, starts_at, ends_at, reason)

    try:
        if settings.SMTP_TLS:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [patient_email], msg.as_bytes())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [patient_email], msg.as_bytes())
        log.info("Confirmation email sent to %s", patient_email)
    except Exception:
        log.error(
            "Failed to send confirmation email to %s", patient_email, exc_info=True
        )


_RESCHEDULE_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Propuesta de Cambio de Cita</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#d97706,#b45309);padding:32px 40px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-.5px;">MedSync</h1>
          <p style="color:#fef3c7;margin:8px 0 0;font-size:14px;">Propuesta de Cambio de Cita</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 20px;">
            Hola, <strong>{patient_name}</strong>,
          </p>
          <p style="color:#475569;font-size:15px;margin:0 0 28px;line-height:1.6;">
            La clínica ha propuesto un <strong style="color:#d97706;">cambio de horario</strong>
            para tu cita con el Dr. {doctor_name}:
          </p>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f1f5f9;border-radius:8px;padding:0 24px;margin-bottom:28px;">
            <tr><td style="padding:16px 0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Nuevo horario propuesto</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{date_str}</span><br>
              <span style="color:#1e293b;font-size:15px;">{starts_time} &ndash; {ends_time}</span>
            </td></tr>
          </table>
          <p style="color:#475569;font-size:15px;margin:0 0 24px;line-height:1.6;">
            Por favor confirma o rechaza el cambio:
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
            <tr>
              <td width="48%" style="padding-right:8px;">
                <a href="{confirm_url}"
                   style="display:block;background:#16a34a;color:#fff;text-align:center;padding:14px;border-radius:8px;font-size:15px;font-weight:600;text-decoration:none;">
                  Confirmar cambio
                </a>
              </td>
              <td width="48%" style="padding-left:8px;">
                <a href="{reject_url}"
                   style="display:block;background:#dc2626;color:#fff;text-align:center;padding:14px;border-radius:8px;font-size:15px;font-weight:600;text-decoration:none;">
                  Rechazar cambio
                </a>
              </td>
            </tr>
          </table>
          <p style="color:#64748b;font-size:13px;line-height:1.6;margin:0;">
            Si no respondes antes de que el enlace expire, la cita se mantendrá en su horario original
            y el equipo de la clínica se pondrá en contacto contigo.
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #e2e8f0;">
          <p style="color:#94a3b8;font-size:12px;margin:0;">
            Mensaje automático de MedSync &mdash; no responder a este correo.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>
"""

_RESCHEDULE_TEXT = """\
Propuesta de Cambio de Cita — MedSync
=======================================

Hola, {patient_name}.

La clínica ha propuesto un cambio de horario para tu cita con el Dr. {doctor_name}:

  Nuevo horario: {date_str}, {starts_time} – {ends_time}

Para CONFIRMAR el cambio, visita:
  {confirm_url}

Para RECHAZAR el cambio, visita:
  {reject_url}

Si no respondes antes de que el enlace expire, la cita se mantendrá en
su horario original y el equipo de la clínica se pondrá en contacto contigo.

— MedSync (mensaje automático, no responder)
"""


def _build_reschedule_mime(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    proposed_starts_at: datetime,
    proposed_ends_at: datetime,
    confirm_url: str,
    reject_url: str,
) -> MIMEMultipart:
    date_str = (
        f"{proposed_starts_at.day} de "
        f"{_ES_MONTHS[proposed_starts_at.month - 1]} de "
        f"{proposed_starts_at.year}"
    )
    starts_time = _fmt_time(proposed_starts_at)
    ends_time = _fmt_time(proposed_ends_at)

    html = _RESCHEDULE_HTML.format(
        patient_name=patient_name,
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        ends_time=ends_time,
        confirm_url=confirm_url,
        reject_url=reject_url,
    )
    text = _RESCHEDULE_TEXT.format(
        patient_name=patient_name,
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        ends_time=ends_time,
        confirm_url=confirm_url,
        reject_url=reject_url,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Cambio de cita propuesto — Dr. {doctor_name}"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = patient_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    return msg


def _send_reschedule_sync(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    proposed_starts_at: datetime,
    proposed_ends_at: datetime,
    confirm_url: str,
    reject_url: str,
) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        log.warning("SMTP not configured — skipping reschedule proposal email.")
        return

    msg = _build_reschedule_mime(
        patient_email, patient_name, doctor_name,
        proposed_starts_at, proposed_ends_at, confirm_url, reject_url,
    )

    try:
        if settings.SMTP_TLS:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [patient_email], msg.as_bytes())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [patient_email], msg.as_bytes())
        log.info("Reschedule proposal email sent to %s", patient_email)
    except Exception:
        log.error("Failed to send reschedule proposal email to %s", patient_email, exc_info=True)


def send_reschedule_proposal(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    proposed_starts_at: datetime,
    proposed_ends_at: datetime,
    confirm_url: str,
    reject_url: str,
    background_tasks: BackgroundTasks,
) -> None:
    if not patient_email:
        return
    background_tasks.add_task(
        _send_reschedule_sync,
        patient_email,
        patient_name,
        doctor_name,
        proposed_starts_at,
        proposed_ends_at,
        confirm_url,
        reject_url,
    )


_RESCHEDULE_REQ_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Solicitud de Reagendado</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#d97706,#b45309);padding:32px 40px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-.5px;">MedSync</h1>
          <p style="color:#fef3c7;margin:8px 0 0;font-size:14px;">Solicitud de Reagendado</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 20px;">
            El paciente <strong>{patient_name}</strong> solicita reagendar su cita.
          </p>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f1f5f9;border-radius:8px;padding:0 24px;margin-bottom:28px;">
            <tr><td style="padding:16px 0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Doctor</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">Dr. {doctor_name}</span>
            </td></tr>
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Cita actual</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{date_str} &mdash; {starts_time}</span>
            </td></tr>
            {note_row}
          </table>
          <p style="color:#64748b;font-size:13px;line-height:1.6;margin:0;">
            Por favor contacta al paciente para confirmar el nuevo horario.
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #e2e8f0;">
          <p style="color:#94a3b8;font-size:12px;margin:0;">
            Mensaje autom&aacute;tico de MedSync &mdash; no responder a este correo.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>
"""

_RESCHEDULE_REQ_NOTE_ROW = """\
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Nota del paciente</span><br>
              <span style="color:#1e293b;font-size:15px;">{note}</span>
            </td></tr>"""

_RESCHEDULE_REQ_TEXT = """\
Solicitud de Reagendado — MedSync
===================================

El paciente {patient_name} solicita reagendar su cita.

  Doctor     : Dr. {doctor_name}
  Cita actual: {date_str} — {starts_time}{note_line}

Por favor contacta al paciente para confirmar el nuevo horario.

— MedSync (mensaje automático, no responder)
"""


def _send_reschedule_req_sync(
    notify_email: str,
    patient_name: str,
    doctor_name: str,
    starts_at: datetime,
    patient_note: str | None,
) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        log.warning("SMTP not configured — skipping reschedule request notification.")
        return

    date_str = f"{starts_at.day} de {_ES_MONTHS[starts_at.month - 1]} de {starts_at.year}"
    starts_time = _fmt_time(starts_at)
    note_row = _RESCHEDULE_REQ_NOTE_ROW.format(note=patient_note) if patient_note else ""
    note_line = f"\n  Nota      : {patient_note}" if patient_note else ""

    html = _RESCHEDULE_REQ_HTML.format(
        patient_name=patient_name,
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        note_row=note_row,
    )
    text = _RESCHEDULE_REQ_TEXT.format(
        patient_name=patient_name,
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        note_line=note_line,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Solicitud de reagendado — {patient_name}"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = notify_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if settings.SMTP_TLS:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [notify_email], msg.as_bytes())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [notify_email], msg.as_bytes())
        log.info("Reschedule request notification sent to %s", notify_email)
    except Exception:
        log.error("Failed to send reschedule request notification to %s", notify_email, exc_info=True)


def send_reschedule_request_notification(
    notify_email: str,
    patient_name: str,
    doctor_name: str,
    starts_at: datetime,
    patient_note: str | None,
    background_tasks: BackgroundTasks,
) -> None:
    if not notify_email:
        return
    background_tasks.add_task(
        _send_reschedule_req_sync,
        notify_email,
        patient_name,
        doctor_name,
        starts_at,
        patient_note,
    )


_RESET_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Recuperación de Contraseña</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#2563eb,#1d4ed8);padding:32px 40px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-.5px;">MedSync</h1>
          <p style="color:#bfdbfe;margin:8px 0 0;font-size:14px;">Recuperación de Contraseña</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 16px;">
            Recibimos una solicitud para restablecer la contraseña de tu cuenta.
          </p>
          <p style="color:#475569;font-size:15px;margin:0 0 28px;line-height:1.6;">
            Haz clic en el botón de abajo para crear una nueva contraseña.
            Este enlace expira en <strong>1 hora</strong>.
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
            <tr>
              <td align="center">
                <a href="{reset_url}"
                   style="display:inline-block;background:#2563eb;color:#fff;text-align:center;
                          padding:14px 32px;border-radius:8px;font-size:15px;font-weight:600;
                          text-decoration:none;letter-spacing:-.01em;">
                  Restablecer contraseña
                </a>
              </td>
            </tr>
          </table>
          <p style="color:#64748b;font-size:13px;line-height:1.6;margin:0 0 8px;">
            Si no solicitaste este cambio, puedes ignorar este correo; tu contraseña no cambiará.
          </p>
          <p style="color:#94a3b8;font-size:12px;line-height:1.6;margin:0;word-break:break-all;">
            O copia este enlace en tu navegador:<br>
            <span style="color:#2563eb;">{reset_url}</span>
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #e2e8f0;">
          <p style="color:#94a3b8;font-size:12px;margin:0;">
            Mensaje autom&aacute;tico de MedSync &mdash; no responder a este correo.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>
"""

_RESET_TEXT = """\
Recuperación de Contraseña — MedSync
======================================

Recibimos una solicitud para restablecer la contraseña de tu cuenta.

Para crear una nueva contraseña, visita el siguiente enlace (expira en 1 hora):

  {reset_url}

Si no solicitaste este cambio, ignora este correo; tu contraseña no cambiará.

— MedSync (mensaje automático, no responder)
"""


def _send_reset_sync(to_email: str, reset_url: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        log.warning("SMTP not configured — skipping password reset email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Recuperación de contraseña — MedSync"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(_RESET_TEXT.format(reset_url=reset_url), "plain", "utf-8"))
    msg.attach(MIMEText(_RESET_HTML.format(reset_url=reset_url), "html", "utf-8"))

    try:
        if settings.SMTP_TLS:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [to_email], msg.as_bytes())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [to_email], msg.as_bytes())
        log.info("Password reset email sent to %s", to_email)
    except Exception:
        log.error("Failed to send password reset email to %s", to_email, exc_info=True)


def send_password_reset_email(
    to_email: str,
    reset_url: str,
    background_tasks: BackgroundTasks,
) -> None:
    if not to_email:
        return
    background_tasks.add_task(_send_reset_sync, to_email, reset_url)


def send_appointment_confirmation(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None,
    background_tasks: BackgroundTasks,
) -> None:
    if not patient_email:
        return
    background_tasks.add_task(
        _send_sync,
        patient_email,
        patient_name,
        doctor_name,
        starts_at,
        ends_at,
        reason,
    )


# ── Self-booking confirmation (includes magic link) ───────────────────────────

_BOOKING_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Cita Agendada</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#0d9488,#0f766e);padding:32px 40px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-.5px;">{clinic_name}</h1>
          <p style="color:#ccfbf1;margin:8px 0 0;font-size:14px;">Confirmaci&oacute;n de Cita</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 20px;">
            Hola, <strong>{patient_name}</strong>,
          </p>
          <p style="color:#475569;font-size:15px;margin:0 0 28px;line-height:1.6;">
            Tu cita ha sido <strong style="color:#0d9488;">agendada exitosamente</strong>.
          </p>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="background:#f1f5f9;border-radius:8px;padding:0 24px;margin-bottom:28px;">
            <tr><td style="padding:16px 0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Doctor</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{doctor_title} {doctor_name}</span>
            </td></tr>
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Fecha</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{date_str}</span>
            </td></tr>
            <tr><td style="padding:16px 0;border-top:1px solid #e2e8f0;">
              <span style="color:#64748b;font-size:12px;text-transform:uppercase;letter-spacing:.5px;">Horario</span><br>
              <span style="color:#1e293b;font-size:16px;font-weight:600;">{starts_time} &ndash; {ends_time}</span>
            </td></tr>
            {reason_row}
          </table>
          <p style="color:#475569;font-size:15px;margin:0 0 20px;line-height:1.6;">
            Usa el siguiente enlace para <strong>confirmar tu asistencia</strong>,
            cancelar o solicitar un cambio de horario:
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:28px;">
            <tr><td style="text-align:center;">
              <a href="{magic_link_url}"
                 style="display:inline-block;background:#0d9488;color:#fff;text-align:center;
                        padding:14px 32px;border-radius:8px;font-size:15px;font-weight:600;
                        text-decoration:none;">
                Ver mi cita
              </a>
            </td></tr>
          </table>
          <p style="color:#94a3b8;font-size:12px;line-height:1.6;margin:0;">
            Este enlace es v&aacute;lido por 72 horas.
            Si no agendaste esta cita, puedes ignorar este correo.
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #e2e8f0;">
          <p style="color:#94a3b8;font-size:12px;margin:0;">
            Mensaje autom&aacute;tico de {clinic_name} &mdash; no responder a este correo.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

_BOOKING_TEXT = """\
Cita Agendada — {clinic_name}
==============================

Hola, {patient_name}.

Tu cita ha sido agendada exitosamente.

  Doctor  : {doctor_title} {doctor_name}
  Fecha   : {date_str}
  Horario : {starts_time} – {ends_time}{reason_line}

Usa el siguiente enlace para confirmar tu asistencia, cancelar o solicitar un cambio:
  {magic_link_url}

Este enlace es válido por 72 horas.

— {clinic_name} (mensaje automático, no responder)
"""


def _send_booking_sync(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    clinic_name: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None,
    magic_link_url: str,
) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        log.warning("SMTP not configured — skipping booking confirmation email.")
        return

    date_str = f"{starts_at.day} de {_ES_MONTHS[starts_at.month - 1]} de {starts_at.year}"
    starts_time = _fmt_time(starts_at)
    ends_time = _fmt_time(ends_at)
    reason_row = _REASON_ROW.format(reason=reason) if reason else ""
    reason_line = f"\n  Motivo  : {reason}" if reason else ""

    html = _BOOKING_HTML.format(
        clinic_name=clinic_name,
        patient_name=patient_name,
        doctor_title="Dr.",
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        ends_time=ends_time,
        reason_row=reason_row,
        magic_link_url=magic_link_url,
    )
    text = _BOOKING_TEXT.format(
        clinic_name=clinic_name,
        patient_name=patient_name,
        doctor_title="Dr.",
        doctor_name=doctor_name,
        date_str=date_str,
        starts_time=starts_time,
        ends_time=ends_time,
        reason_line=reason_line,
        magic_link_url=magic_link_url,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Tu cita está confirmada — {clinic_name}"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = patient_email
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if settings.SMTP_TLS:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [patient_email], msg.as_bytes())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [patient_email], msg.as_bytes())
        log.info("Booking confirmation email sent to %s", patient_email)
    except Exception:
        log.error("Failed to send booking confirmation email to %s", patient_email, exc_info=True)


_VERIFY_HTML = """\
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Código de Verificación</title>
</head>
<body style="margin:0;padding:0;background:#f4f7fb;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f7fb;padding:40px 0;">
  <tr><td align="center">
    <table width="600" cellpadding="0" cellspacing="0"
           style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);">
      <tr>
        <td style="background:linear-gradient(135deg,#2563eb,#1d4ed8);padding:32px 40px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;font-weight:700;letter-spacing:-.5px;">MedSync</h1>
          <p style="color:#bfdbfe;margin:8px 0 0;font-size:14px;">Verificación de correo electrónico</p>
        </td>
      </tr>
      <tr>
        <td style="padding:40px;text-align:center;">
          <p style="color:#1e293b;font-size:16px;margin:0 0 24px;line-height:1.6;">
            Usa el siguiente código para verificar tu correo y completar tu reserva:
          </p>
          <div style="background:#f1f5f9;border-radius:12px;padding:24px;margin:0 auto 24px;display:inline-block;min-width:200px;">
            <span style="font-size:40px;font-weight:800;letter-spacing:10px;color:#1e293b;font-family:monospace;">{code}</span>
          </div>
          <p style="color:#64748b;font-size:14px;margin:0 0 8px;">
            Este c&oacute;digo es v&aacute;lido por <strong>15 minutos</strong>.
          </p>
          <p style="color:#94a3b8;font-size:13px;margin:0;">
            Si no solicitaste esto, puedes ignorar este correo.
          </p>
        </td>
      </tr>
      <tr>
        <td style="background:#f8fafc;padding:20px 40px;text-align:center;border-top:1px solid #e2e8f0;">
          <p style="color:#94a3b8;font-size:12px;margin:0;">
            Mensaje autom&aacute;tico de MedSync &mdash; no responder a este correo.
          </p>
        </td>
      </tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""

_VERIFY_TEXT = """\
Código de Verificación — MedSync
==================================

Tu código de verificación es:

  {code}

Es válido por 15 minutos.

Si no solicitaste esto, ignora este correo.

— MedSync (mensaje automático, no responder)
"""


def _send_verification_sync(to_email: str, code: str) -> None:
    if not settings.SMTP_HOST or not settings.SMTP_USER:
        log.warning("SMTP not configured — skipping verification code email.")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Tu código de verificación: {code}"
    msg["From"] = settings.EMAILS_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(_VERIFY_TEXT.format(code=code), "plain", "utf-8"))
    msg.attach(MIMEText(_VERIFY_HTML.format(code=code), "html", "utf-8"))

    try:
        if settings.SMTP_TLS:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [to_email], msg.as_bytes())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.sendmail(settings.EMAILS_FROM, [to_email], msg.as_bytes())
        log.info("Verification code email sent to %s", to_email)
    except Exception:
        log.error("Failed to send verification code email to %s", to_email, exc_info=True)


def send_verification_code_email(
    to_email: str,
    code: str,
    background_tasks: BackgroundTasks,
) -> None:
    if not to_email:
        return
    background_tasks.add_task(_send_verification_sync, to_email, code)


def send_booking_confirmation(
    patient_email: str,
    patient_name: str,
    doctor_name: str,
    clinic_name: str,
    starts_at: datetime,
    ends_at: datetime,
    reason: str | None,
    magic_link_url: str,
    background_tasks: BackgroundTasks,
) -> None:
    if not patient_email:
        return
    background_tasks.add_task(
        _send_booking_sync,
        patient_email,
        patient_name,
        doctor_name,
        clinic_name,
        starts_at,
        ends_at,
        reason,
        magic_link_url,
    )
