"""
Gateway-side re-export shim for the Clerk JWKS validator.

The validator's body lives in sdk/common/clerk_auth.py so both the
gateway (this service) and the identity service can import it without a
cross-service Python dependency. This file exists only to keep the
import path that other gateway modules already use working without
churn (services/gateway/auth.py and tests/test_clerk_validator.py both
reach in here).
"""

from __future__ import annotations

from sdk.common.clerk_auth import (
    ClerkTokenValidator,
    get_clerk_validator,
    looks_like_clerk_token,
    normalize_clerk_role,
)
# Re-exported state symbols so tests that reach in for
# `auth_clerk._jwks_cache = None` keep working. The symbols here are
# bound to the source module's attributes at import time; tests that
# need to mutate them at module level should target sdk.common.clerk_auth
# directly (which the new tests/test_clerk_validator.py does).
from sdk.common.clerk_auth import (  # noqa: F401
    REDIS_JWKS_CACHE_KEY,
    _get_jwks_cache,
)

__all__ = [
    "ClerkTokenValidator",
    "get_clerk_validator",
    "looks_like_clerk_token",
    "normalize_clerk_role",
]
