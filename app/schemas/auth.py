from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator, model_validator


class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "assistant"
    clinic_id: Optional[uuid.UUID] = None
    clinic_name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        if v not in ("owner", "doctor", "assistant"):
            raise ValueError("Role must be one of: owner, doctor, assistant.")
        return v

    @model_validator(mode="after")
    def clinic_required(self) -> "UserRegister":
        if self.clinic_id is None and self.clinic_name is None:
            raise ValueError("Provide either clinic_id (join existing) or clinic_name (create new).")
        if self.clinic_id is not None and self.clinic_name is not None:
            raise ValueError("Provide either clinic_id or clinic_name, not both.")
        return self


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    clinic_id: Optional[uuid.UUID] = None


class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenData(BaseModel):
    email: str
    clinic_id: uuid.UUID
    role: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("La contraseña debe tener al menos 8 caracteres.")
        return v


class MessageResponse(BaseModel):
    message: str
