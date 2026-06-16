from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.models.api_key import APIKey
from services.api.schemas.api_key import APIKeyCreate, EmployeeKeyCreate


class APIKeyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(
        self, tenant_id: uuid.UUID, payload: APIKeyCreate
    ) -> tuple[APIKey, str]:
        """
        Generates a new raw API key, stores its hash, and returns both.
        The raw key is NEVER stored.
        """
        # Generate 32-byte secure random key
        raw_key = f"acp_{secrets.token_urlsafe(32)}"
        key_prefix = raw_key[:8]
        key_hash = self._hash_key(raw_key)

        api_key = APIKey(
            tenant_id=tenant_id,
            name=payload.name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=payload.expires_at,
            subject_kind="tenant",
        )

        self.db.add(api_key)
        await self.db.commit()
        await self.db.refresh(api_key)

        return api_key, raw_key

    async def create_employee_key(
        self, tenant_id: uuid.UUID, payload: EmployeeKeyCreate,
    ) -> tuple[APIKey, str]:
        """Mint an ``acp_emp_…`` virtual key for one employee.

        Same hashing + persistence as a tenant key, but the row is tagged
        with ``subject_kind='employee'`` and carries the employee email +
        budget caps. The gateway's /v1/messages proxy reads those fields
        to attribute spend per-human and to refuse over-budget calls
        before hitting upstream Anthropic.
        """
        raw_key = f"acp_emp_{secrets.token_urlsafe(32)}"
        # acp_emp_ is 8 chars; first 12 give a useful display prefix
        key_prefix = raw_key[:12]
        key_hash = self._hash_key(raw_key)

        email = payload.email.strip().lower()
        display_name = (payload.name or email.split("@", 1)[0])[:100]

        api_key = APIKey(
            tenant_id=tenant_id,
            name=display_name,
            key_prefix=key_prefix,
            key_hash=key_hash,
            expires_at=payload.expires_at,
            subject_kind="employee",
            subject_email=email,
            daily_budget_usd=payload.daily_budget_usd,
            monthly_budget_usd=payload.monthly_budget_usd,
        )

        self.db.add(api_key)
        await self.db.commit()
        await self.db.refresh(api_key)
        return api_key, raw_key

    async def list_for_tenant(
        self, tenant_id: uuid.UUID, subject_kind: str | None = None,
    ) -> Sequence[APIKey]:
        """List active keys for a tenant. Pass ``subject_kind='employee'``
        for the Team page; the default returns every kind so the legacy
        Developer panel keeps working."""
        stmt = select(APIKey).where(APIKey.tenant_id == tenant_id, APIKey.is_active)
        if subject_kind:
            stmt = stmt.where(APIKey.subject_kind == subject_kind)
        result = await self.db.execute(stmt)
        return result.scalars().all()

    async def deactivate(self, tenant_id: uuid.UUID, key_id: uuid.UUID) -> bool:
        stmt = (
            update(APIKey)
            .where(APIKey.id == key_id, APIKey.tenant_id == tenant_id)
            .values(is_active=False)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount > 0

    async def get_by_hash(self, key_hash: str) -> APIKey | None:
        stmt = select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def validate(self, raw_key: str) -> APIKey | None:
        """Verify if a raw API key is valid."""
        key_hash = self._hash_key(raw_key)
        api_key = await self.get_by_hash(key_hash)

        if not api_key:
            return None

        # Check expiration
        if api_key.expires_at and api_key.expires_at < datetime.now(
            tz=api_key.expires_at.tzinfo
        ):
            return None

        return api_key

    @staticmethod
    def _hash_key(key: str) -> str:
        """One-way hash for API keys."""
        return hashlib.sha256(key.encode()).hexdigest()
