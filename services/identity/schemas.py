from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr

# =========================
# REQUEST SCHEMAS
# =========================


class AgentLoginRequest(BaseModel):
    """Agent presents its ID + secret to obtain a JWT."""

    agent_id: uuid.UUID
    secret: SecretStr = Field(..., min_length=16, max_length=256)

    model_config = ConfigDict(from_attributes=True)



class CredentialCreateRequest(BaseModel):
    """Admin provisions credentials for an agent."""

    agent_id: uuid.UUID
    secret: SecretStr = Field(
        ...,
        min_length=16,
        max_length=256,
        description="Raw secret; will be hashed before storage",
    )

    model_config = ConfigDict(from_attributes=True)


# =========================
# RESPONSE SCHEMAS
# =========================


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    tenant_id: uuid.UUID
    role: str = "agent"


class CredentialResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str | None = None
    role: str
    tenant_id: uuid.UUID
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RevokeResponse(BaseModel):
    agent_id: uuid.UUID
    revoked: bool
    message: str


# =========================
# INTROSPECTION
# =========================


class TokenIntrospectRequest(BaseModel):
    token: str


class TokenIntrospectResponse(BaseModel):
    active: bool
    agent_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    role: str | None = None
    exp: int | None = None
    iat: int | None = None


class UserCreate(BaseModel):
    email: str
    password: str = Field(min_length=8)
    tenant_id: str
    org_id: str | None = None  # if omitted, defaults to tenant_id in the router
    role: str = "user"


class UserLogin(BaseModel):
    email: str
    password: str


# ─── Self-serve registration + password ops (OSS auth, no Clerk) ───

class UserRegister(BaseModel):
    """Public self-serve signup: creates Organization + Tenant + OWNER User."""
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=256)
    workspace_name: str | None = Field(default=None, max_length=120)
    full_name: str | None = Field(default=None, max_length=255)


class PasswordResetRequest(BaseModel):
    """Request a password-reset link. Response is always 202 to prevent
    account enumeration; the reset token is only delivered out-of-band
    (server logs in OSS mode, email hook in hosted mode)."""
    email: str = Field(min_length=3, max_length=255)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=16, max_length=2048)
    new_password: str = Field(min_length=8, max_length=256)


class PasswordChange(BaseModel):
    """Authenticated password change — requires the current password."""
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)
