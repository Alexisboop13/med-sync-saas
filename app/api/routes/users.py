from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, OwnerOnly, Role, TenantContext
from app.core.crypto import decrypt, encrypt, make_search_hash
from app.core.limiter import limiter
from app.core.security import get_password_hash, verify_password
from app.models.audit_log import EventType
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.audit import log_audit

router = APIRouter(prefix="/users", tags=["Users"])


class UserListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: uuid.UUID
    full_name: str
    email: str
    role: str
    is_active: bool


class UserRolePatch(BaseModel):
    role: Literal["doctor", "assistant"]


class UserPatch(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(None, max_length=30)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8)


@router.get("", response_model=List[UserListItem])
@limiter.limit("30/minute")
async def list_users(
    request: Request,
    ctx: TenantContext,
    _: OwnerOnly,
):
    result = await ctx.db.execute(
        select(User).where(User.clinic_id == ctx.clinic_id, User.is_active.is_(True))
    )
    users = result.scalars().all()
    out: List[UserListItem] = []
    for u in users:
        try:
            name = decrypt(u.full_name_enc)
        except Exception:
            name = u.full_name_enc
        try:
            email_val = decrypt(u.email_enc) if u.email_enc else ""
        except Exception:
            email_val = ""
        out.append(UserListItem(id=u.id, full_name=name, email=email_val, role=u.role, is_active=u.is_active))
    return out


@router.patch("/{user_id}/role", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def update_user_role(
    request: Request,
    user_id: uuid.UUID,
    body: UserRolePatch,
    ctx: TenantContext,
    current_user: OwnerOnly,
):
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot change your own role.",
        )

    result = await ctx.db.execute(
        select(User).where(User.id == user_id, User.clinic_id == ctx.clinic_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if user.role == Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot change the role of an owner.",
        )

    prev_role = user.role
    user.role = body.role
    await log_audit(
        ctx.db,
        event_type=EventType.USER_ROLE_CHANGED,
        entity_type="User",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=user.id,
        source=request.url.path,
        data={
            "before": {"role": prev_role},
            "after": {"role": body.role},
            "target_user_id": str(user.id),
        },
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()


@router.patch("/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def deactivate_user(
    request: Request,
    user_id: uuid.UUID,
    ctx: TenantContext,
    current_user: OwnerOnly,
):
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account.",
        )

    result = await ctx.db.execute(
        select(User).where(User.id == user_id, User.clinic_id == ctx.clinic_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already deactivated.",
        )

    user.is_active = False

    # Revoke all active refresh tokens so existing sessions are invalidated immediately.
    tokens_result = await ctx.db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.clinic_id == ctx.clinic_id,
            RefreshToken.revoked_at.is_(None),
        )
    )
    for token in tokens_result.scalars().all():
        token.revoked_at = datetime.now(timezone.utc)

    await log_audit(
        ctx.db,
        event_type=EventType.USER_DEACTIVATED,
        entity_type="User",
        clinic_id=ctx.clinic_id,
        actor_id=current_user.id,
        actor_role=current_user.role,
        entity_id=user_id,
        source=request.url.path,
        data={"target_user_id": str(user_id)},
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await ctx.db.commit()


@router.patch("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def update_user(
    request: Request,
    user_id: uuid.UUID,
    body: UserPatch,
    ctx: TenantContext,
    current_user: CurrentUser,
):
    is_owner = current_user.role == Role.OWNER
    is_self  = current_user.id == user_id

    if not is_owner and not is_self:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own profile.",
        )

    result = await ctx.db.execute(
        select(User).where(User.id == user_id, User.clinic_id == ctx.clinic_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if body.email is not None:
        email_lower = body.email.lower().strip()
        email_hash  = make_search_hash(email_lower)
        conflict = await ctx.db.execute(
            select(User).where(
                User.clinic_id == ctx.clinic_id,
                User.email_search_hash == email_hash,
                User.id != user_id,
            )
        )
        if conflict.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already in use by another user in this clinic.",
            )
        ciphertext, _ = encrypt(email_lower)
        user.email_enc         = ciphertext
        user.email_search_hash = email_hash

    if body.full_name is not None:
        ciphertext, _ = encrypt(body.full_name.strip())
        user.full_name_enc = ciphertext

    if body.phone is not None:
        if body.phone.strip():
            ciphertext, _ = encrypt(body.phone.strip())
            user.phone_enc = ciphertext
        else:
            user.phone_enc = None

    await ctx.db.commit()


@router.patch("/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    user_id: uuid.UUID,
    body: PasswordChange,
    ctx: TenantContext,
    current_user: CurrentUser,
):
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only change your own password.",
        )

    result = await ctx.db.execute(
        select(User).where(User.id == user_id, User.clinic_id == ctx.clinic_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    if not verify_password(body.current_password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    user.hashed_password = get_password_hash(body.new_password)
    await ctx.db.commit()
