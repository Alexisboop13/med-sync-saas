import jwt
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])


def get_ip_and_clinic(request: Request) -> str:
    ip = get_remote_address(request)
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:]
        try:
            from app.core.config import settings  # noqa: PLC0415
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": False},
            )
            clinic_id = payload.get("clinic_id")
            if clinic_id:
                return f"{ip}:{clinic_id}"
        except jwt.PyJWTError:
            pass
    return ip
