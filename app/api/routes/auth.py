from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.core.config import settings
from app.core.crypto import decrypt, encrypt, make_search_hash
from app.core.email import send_password_reset_email
from app.core.limiter import limiter
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    get_password_hash,
    hash_refresh_token,
    verify_password,
)
from app.models.audit_log import EventType
from app.models.clinic import Clinic
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.audit import log_audit
from app.schemas.auth import (
    ForgotPasswordRequest,
    LogoutRequest,
    MessageResponse,
    RefreshTokenRequest,
    ResetPasswordRequest,
    Token,
    UserLogin,
    UserRegister,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


async def _require_owner_of_clinic(
    clinic_id: uuid.UUID,
    credentials: HTTPAuthorizationCredentials | None,
    db: AsyncSession,
) -> None:
    """Verify the caller holds a valid JWT and is an owner of clinic_id."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required to add users to an existing clinic.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        caller_id = uuid.UUID(payload["sub"])
        caller_clinic_id = uuid.UUID(payload["clinic_id"])
        caller_role = payload["role"]
    except (KeyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if caller_clinic_id != clinic_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to add users to this clinic.",
        )

    if caller_role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only clinic owners can register new users.",
        )

    result = await db.execute(
        select(User).where(
            User.id == caller_id,
            User.clinic_id == caller_clinic_id,
            User.is_active.is_(True),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Caller account not found or deactivated.",
        )


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug.strip("-")[:80]


async def _unique_slug(db: AsyncSession, base: str) -> str:
    slug = base
    suffix = 0
    while True:
        result = await db.execute(select(Clinic).where(Clinic.slug == slug))
        if result.scalar_one_or_none() is None:
            return slug
        suffix += 1
        slug = f"{base}-{suffix}"


async def _create_tokens(db: AsyncSession, user: User) -> Token:
    access_token = create_access_token(
        subject=user.id, clinic_id=user.clinic_id, role=user.role
    )
    raw_refresh, token_hash, expires_at = generate_refresh_token()
    db.add(
        RefreshToken(
            clinic_id=user.clinic_id,
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    await db.commit()
    return Token(access_token=access_token, refresh_token=raw_refresh)


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
async def register(
    request: Request,
    body: UserRegister,
    db: AsyncSession = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    email_lower = body.email.lower().strip()
    email_hash = make_search_hash(email_lower)

    if body.clinic_name:
        slug = await _unique_slug(db, _slugify(body.clinic_name))
        clinic = Clinic(name=body.clinic_name, slug=slug)
        db.add(clinic)
        await db.flush()
        clinic_id = clinic.id
        role = "owner"
    else:
        clinic_id = body.clinic_id
        role = body.role

        if role == "owner":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot self-assign the owner role.",
            )

        await _require_owner_of_clinic(clinic_id, credentials, db)

        result = await db.execute(select(Clinic).where(Clinic.id == clinic_id))
        if result.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clinic not found.")

    dup = await db.execute(
        select(User).where(
            User.clinic_id == clinic_id,
            User.email_search_hash == email_hash,
        )
    )
    if dup.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists in this clinic.",
        )

    email_ciphertext, _ = encrypt(email_lower)
    name_ciphertext, _ = encrypt(body.full_name)

    user = User(
        clinic_id=clinic_id,
        email_enc=email_ciphertext,
        email_search_hash=email_hash,
        full_name_enc=name_ciphertext,
        hashed_password=get_password_hash(body.password),
        role=role,
    )
    db.add(user)
    await db.flush()

    return await _create_tokens(db, user)


@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
async def login(request: Request, body: UserLogin, db: AsyncSession = Depends(get_db)):
    email_lower = body.email.lower().strip()
    email_hash = make_search_hash(email_lower)

    stmt = select(User).where(User.email_search_hash == email_hash)
    if body.clinic_id is not None:
        stmt = stmt.where(User.clinic_id == body.clinic_id)

    result = await db.execute(stmt)
    users = result.scalars().all()

    if len(users) == 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    if len(users) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Multiple accounts found for this email. Please provide clinic_id.",
        )

    user = users[0]

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is deactivated.")

    if not verify_password(body.password, user.hashed_password):
        await log_audit(
            db,
            event_type=EventType.USER_LOGIN_FAILED,
            entity_type="User",
            clinic_id=user.clinic_id,
            actor_id=user.id,
            actor_role=user.role,
            entity_id=user.id,
            source=request.url.path,
            data={"reason": "invalid_password"},
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")

    await log_audit(
        db,
        event_type=EventType.USER_LOGIN,
        entity_type="User",
        clinic_id=user.clinic_id,
        actor_id=user.id,
        actor_role=user.role,
        entity_id=user.id,
        source=request.url.path,
        data={"actor_role": user.role},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return await _create_tokens(db, user)


@router.post("/refresh-token", response_model=Token)
@limiter.limit("60/minute")
async def refresh_token(request: Request, body: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    if stored is None or not stored.is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    result = await db.execute(
        select(User).where(
            User.id == stored.user_id,
            User.clinic_id == stored.clinic_id,
        )
    )
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    stored.revoked_at = datetime.now(timezone.utc)
    await db.flush()

    await log_audit(
        db,
        event_type=EventType.USER_TOKEN_REFRESHED,
        entity_type="User",
        clinic_id=user.clinic_id,
        actor_id=user.id,
        actor_role=user.role,
        entity_id=user.id,
        source=request.url.path,
        data={"actor_role": user.role},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return await _create_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def logout(request: Request, body: LogoutRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(timezone.utc)
        await log_audit(
            db,
            event_type=EventType.USER_LOGOUT,
            entity_type="User",
            clinic_id=stored.clinic_id,
            actor_id=stored.user_id,
            source=request.url.path,
            data={},
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        await db.commit()


_GENERIC_RESET_MSG = "Si el correo existe en nuestro sistema, recibirás un enlace de recuperación en breve."

_RESET_RATE_LIMIT = 3


@router.post("/forgot-password", response_model=MessageResponse)
@limiter.limit("5/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    email_lower = body.email.lower().strip()
    email_hash = make_search_hash(email_lower)

    result = await db.execute(select(User).where(User.email_search_hash == email_hash))
    users = result.scalars().all()

    if not users:
        return MessageResponse(message=_GENERIC_RESET_MSG)

    user = users[0]

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    count_result = await db.execute(
        select(func.count()).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.created_at >= one_hour_ago,
        )
    )
    if (count_result.scalar() or 0) >= _RESET_RATE_LIMIT:
        return MessageResponse(message=_GENERIC_RESET_MSG)

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    db.add(
        PasswordResetToken(
            clinic_id=user.clinic_id,
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    await db.commit()

    reset_url = f"{settings.APP_BASE_URL}/?reset_token={raw_token}"
    send_password_reset_email(
        to_email=decrypt(user.email_enc),
        reset_url=reset_url,
        background_tasks=background_tasks,
    )

    return MessageResponse(message=_GENERIC_RESET_MSG)


@router.post("/reset-password", response_model=MessageResponse)
@limiter.limit("10/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()

    _invalid = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="El enlace de recuperación es inválido o ha expirado.",
    )

    if stored is None or not stored.is_valid:
        raise _invalid

    result = await db.execute(select(User).where(User.id == stored.user_id))
    user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        raise _invalid

    user.hashed_password = get_password_hash(body.new_password)
    stored.used_at = datetime.now(timezone.utc)
    await db.commit()

    return MessageResponse(message="Contraseña actualizada exitosamente.")
