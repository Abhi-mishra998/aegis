from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class APIKeyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_at: datetime | None = None
    # Sprint 1.5 — optional per-agent scope. When set, the gateway requires
    # the inbound X-Agent-ID header to match this value (so a leaked
    # per-agent key can never be used to impersonate a different agent).
    agent_id: uuid.UUID | None = None


class APIKeyCreate(APIKeyBase):
    pass


# Sprint 17 — Employee virtual-key minting. Same underlying APIKey row but
# tagged with subject_kind='employee', a subject_email for spend rollup,
# and optional per-employee budget caps. Mints an `acp_emp_…` prefix so
# the gateway's auth path can fast-path it into the Anthropic-proxy flow.
class EmployeeKeyCreate(BaseModel):
    email: EmailStr = Field(..., description="Employee's company email — the spend-rollup identity")
    name: str | None = Field(
        default=None,
        max_length=100,
        description="Display name. Defaults to the email's local-part.",
    )
    # Sprint 17.5 — free-text department tag for the /team Department View
    # rollup. Suggested values match the spec (Engineering / Finance /
    # Legal / Sales / Support) but any string is accepted so customers
    # with custom org structures aren't fenced in.
    department: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Department for the Teams Department View rollup. Common values: "
            "Engineering, Finance, Legal, Sales, Support. NULL = Unassigned."
        ),
    )
    daily_budget_usd: float | None = Field(
        default=None,
        ge=0,
        description="Hard daily cap in USD. The gateway 429s further requests when hit. NULL = no cap.",
    )
    monthly_budget_usd: float | None = Field(
        default=None,
        ge=0,
        description="Hard monthly cap in USD. NULL = no cap.",
    )
    expires_at: datetime | None = None


class APIKeyValidateRequest(BaseModel):
    api_key: str


class APIKeyGenerated(APIKeyBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    api_key: str  # The raw key (only returned once)
    key_prefix: str
    created_at: datetime
    subject_kind: Literal["tenant", "agent", "employee"] = "tenant"
    subject_email: str | None = None

    model_config = ConfigDict(from_attributes=True)


class APIKeyResponse(APIKeyBase):
    id: uuid.UUID
    tenant_id: uuid.UUID
    key_prefix: str
    is_active: bool
    created_at: datetime
    last_used_at: datetime | None = None
    subject_kind: Literal["tenant", "agent", "employee"] = "tenant"
    subject_email: str | None = None
    daily_budget_usd: float | None = None
    monthly_budget_usd: float | None = None
    department: str | None = None

    model_config = ConfigDict(from_attributes=True)
