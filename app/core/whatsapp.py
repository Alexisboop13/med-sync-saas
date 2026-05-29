"""
app/core/whatsapp.py
──────────────────────────────────────────────────────────────────────────────
Integración con WhatsApp vía GreenAPI (plan gratuito: 100 mensajes/mes).

Setup de 5 minutos:
1. Regístrate en https://greenapi.com (gratis)
2. Crea una instancia → escanea el QR con tu WhatsApp Business
3. Copia GREENAPI_INSTANCE_ID y GREENAPI_API_TOKEN al .env
4. El número de WhatsApp vinculado será el remitente de los mensajes

Para México: los números deben tener formato 521XXXXXXXXXX@c.us
(52 = código de país, 1 = móvil, 10 dígitos)
"""
from __future__ import annotations

import logging
import re

import httpx

from app.core.config import settings

log = logging.getLogger(__name__)

_GREENAPI_BASE = "https://api.greenapi.com"


def _normalize_phone(phone: str) -> str | None:
    """Convierte un número de teléfono a formato chatId de WhatsApp."""
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


async def send_whatsapp(phone: str, message: str) -> bool:
    """
    Envía un mensaje de WhatsApp vía GreenAPI.
    Devuelve True si el envío fue exitoso, False en cualquier otro caso.
    No lanza excepciones — los errores se loguean.
    """
    if not settings.GREENAPI_INSTANCE_ID or not settings.GREENAPI_API_TOKEN:
        log.debug("WhatsApp desactivado: GREENAPI_INSTANCE_ID o GREENAPI_API_TOKEN no configurados.")
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
