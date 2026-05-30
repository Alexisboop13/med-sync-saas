from fastapi import APIRouter, HTTPException
from app.core.config import settings
import requests

router = APIRouter(tags=["testing"])


@router.post("/test-sendgrid")
async def test_sendgrid():
    """Prueba que SendGrid funciona directamente"""
    try:
        data = {
            "personalizations": [{"to": [{"email": "alexisdehesa@gmail.com"}]}],
            "from": {"email": settings.EMAILS_FROM},
            "subject": "Prueba directa desde Med-Sync",
            "content": [{"type": "text/plain", "value": "Si ves esto, SendGrid funciona correctamente."}]
        }
        response = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={"Authorization": f"Bearer {settings.SENDGRID_API_KEY}"},
            json=data
        )
        if response.status_code == 202:
            return {"status": "success", "message": "Email enviado correctamente"}
        else:
            return {"status": "error", "details": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
