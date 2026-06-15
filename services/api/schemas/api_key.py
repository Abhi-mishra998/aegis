from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class APIKeyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = None
    # Sprint 1.5 — optional per-agent scope. When set, the gateway requires
    # the inbound X-Agent-ID header to match this value (so a leaked
    # per-agent key can never be used to impersonate a different agent).
    agent_id: uuid.UUID | None = None


class APIKeyCreate(APIKeyBase):
    pass


class APIKeyValidateRequest(BaseModel):
    api_key: str


class APIKeyGenerated(APIKeyBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    api_key: str  # The raw key (only returned once)
    key_prefix: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class APIKeyResponse(APIKeyBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
