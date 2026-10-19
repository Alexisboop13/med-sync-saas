"""
app/core/whatsapp.py
──────────────────────────────────────────────────────────────────────────────
WhatsApp tiene dos modos, controlados por `settings.WHATSAPP_MODE`:

  • "manual" (default): el sistema NO envía nada por su cuenta. El consultorio
    abre un atajo wa.me/{numero}?text={mensaje} desde el navegador, revisa el
    mensaje precargado y presiona "Enviar" dentro de WhatsApp Web/App. Cuando
    el paciente responde "SÍ", el staff marca la cita como confirmada a mano
    (PATCH /appointments/{id}/confirm). No requiere cuenta de GreenAPI.

  • "api": envío automático vía GreenAPI (plan gratuito: 100 mensajes/mes).
    Setup de 5 minutos:
      1. Regístrate en https://greenapi.com (gratis)
      2. Crea una instancia → escanea el QR con tu WhatsApp Business
      3. Copia GREENAPI_INSTANCE_ID y GREENAPI_API_TOKEN al .env
      4. Pon WHATSAPP_MODE=api
    El número de WhatsApp vinculado será el remitente de los mensajes.

Para México: los números deben tener formato 521XXXXXXXXXX@c.us
(52 = código de país, 1 = móvil, 10 dígitos).
"""
from __future__ import annotations

import logging
import re
import urllib.parse

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_GREENAPI_BASE = "https://api.greenapi.com"
_INSECURE_PLACEHOLDERS = {"", "CHANGE_ME"}


def whatsapp_api_enabled() -> bool:
    """True solo cuando WHATSAPP_MODE=api y las credenciales de GreenAPI están configuradas."""
    if settings.WHATSAPP_MODE.lower() != "api":
        return False
    return (
        settings.GREENAPI_INSTANCE_ID not in _INSECURE_PLACEHOLDERS
        and settings.GREENAPI_API_TOKEN not in _INSECURE_PLACEHOLDERS
    )


def _normalize_phone(phone: str) -> str | None:
    """Convierte un número de teléfono a formato chatId de WhatsApp (GreenAPI)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None

    if digits.startswith("521") and len(digits) == 13:
        return f"{digits}@c.us"
    if digits.startswith("52") and len(digits) == 12:
        return f"{digits}@c.us"
    if len(digits) == 10:
        return f"521{digits}@c.us"
    if len(digits) >= 11:
        return f"{digits}@c.us"
    return None


def wa_me_phone(phone: str) -> str | None:
    """
    Convierte un número de teléfono al formato que espera wa.me (solo dígitos,
    con código de país, sin '+' ni espacios). Devuelve None si el número no
    se puede normalizar.
    """
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None

    if digits.startswith("521") and len(digits) == 13:
        return f"52{digits[3:]}"
    if digits.startswith("52") and len(digits) == 12:
        return digits
    if len(digits) == 10:
        return f"52{digits}"
    if len(digits) >= 11:
        return digits
    return None


def build_wa_link(phone: str, message: str) -> str | None:
    """Construye el link https://wa.me/{numero}?text={mensaje}. None si el teléfono es inválido."""
    numero = wa_me_phone(phone)
    if numero is None:
        return None
    return f"https://wa.me/{numero}?text={urllib.parse.quote(message, safe='')}"


def format_confirmation_request(
    patient_name: str,
    doctor_name: str,
    clinic_name: str,
    starts_at_local: str,
) -> str:
    primer_nombre = (patient_name or "").strip().split(" ")[0] or patient_name
    return (
        f"Hola {primer_nombre}, te escribimos de *{clinic_name}* 👋\n\n"
        f"Queremos confirmar tu cita:\n"
        f"👨‍⚕️ {doctor_name}\n"
        f"📅 {starts_at_local}\n\n"
        f"¿Nos confirmas tu asistencia? Responde *SÍ* para confirmar o *NO* "
        f"si necesitas cambiarla. ¡Gracias!"
    )


async def send_whatsapp(phone: str, message: str) -> bool:
    """
    Envía un mensaje de WhatsApp vía GreenAPI (solo si whatsapp_api_enabled()).
    Devuelve True si el envío fue exitoso, False en cualquier otro caso.
    No lanza excepciones — los errores se loguean.
    """
    if not whatsapp_api_enabled():
        log.debug("WhatsApp desactivado: WHATSAPP_MODE no es 'api' o faltan credenciales de GreenAPI.")
        return False

    chat_id = _normalize_phone(phone)
    if not chat_id:
        log.warning("send_whatsapp: número de teléfono inválido: %r", phone)
        return False

    url = (
        f"{_GREENAPI_BASE}/waInstance{settings.GREENAPI_INSTANCE_ID}"
        f"/sendMessage/{settings.GREENAPI_API_TOKEN}"
    )
    payload = {"chatId": chat_id, "message": message}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                log.info("WhatsApp enviado a %s", chat_id)
                return True
            log.warning("GreenAPI respondió %s: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        log.error("Error enviando WhatsApp a %s: %s", chat_id, exc)
        return False


def format_appointment_confirmation(
    patient_name: str,
    doctor_name: str,
    clinic_name: str,
    starts_at_local: str,
    magic_link: str,
) -> str:
    return (
        f"✅ *{clinic_name}*\n\n"
        f"Hola {patient_name}, tu cita ha sido *confirmada*.\n\n"
        f"👨‍⚕️ Doctor: {doctor_name}\n"
        f"📅 Fecha y hora: {starts_at_local}\n\n"
        f"Gestiona tu cita (confirmar · cancelar · reagendar):\n"
        f"{magic_link}\n\n"
        f"_Responde a este número si tienes dudas._"
    )


def format_appointment_reminder(
    patient_name: str,
    doctor_name: str,
    clinic_name: str,
    starts_at_local: str,
    magic_link: str,
) -> str:
    return (
        f"⏰ *Recordatorio — {clinic_name}*\n\n"
        f"Hola {patient_name}, tu cita es *mañana*.\n\n"
        f"👨‍⚕️ Doctor: {doctor_name}\n"
        f"📅 {starts_at_local}\n\n"
        f"¿Necesitas cancelar o reagendar?\n"
        f"{magic_link}"
    )


def format_appointment_canceled(
    patient_name: str,
    clinic_name: str,
    starts_at_local: str,
) -> str:
    return (
        f"❌ *{clinic_name}*\n\n"
        f"Hola {patient_name}, tu cita del *{starts_at_local}* "
        f"ha sido cancelada.\n\n"
        f"Contáctanos para reagendar."
    )
