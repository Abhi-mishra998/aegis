"""
Tests for services/gateway/auth.verify_role(*allowed).

The dependency is exercised directly (no FastAPI HTTP client) — we
replace the module-global token_validator with a stub that returns
canned claims so the test surface is purely the gating logic.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from sdk.common.exceptions import ACPAuthError
from sdk.common.roles import Role
from services.gateway import auth as gateway_auth


class _StubValidator:
    """Mimics LocalTokenValidator.validate without any I/O."""

    def __init__(self, claims):
        self._claims = claims

    async def validate(self, token):
        if token == "invalid":
            raise ACPAuthError("signature invalid")
        return self._claims


@pytest.fixture(autouse=True)
def _restore_validator():
    original = gateway_auth.token_validator
    yield
    gateway_auth.token_validator = original


def _run(dep, **kwargs):
    return asyncio.new_event_loop().run_until_complete(dep(**kwargs))


def test_factory_rejects_empty_allowlist():
    with pytest.raises(ValueError, match="empty allowlist"):
        gateway_auth.verify_role()


def test_factory_normalizes_string_aliases():
    """verify_role("admin") and verify_role(Role.ADMIN) must allow the same set."""
    by_string = gateway_auth.verify_role("admin")
    by_enum = gateway_auth.verify_role(Role.ADMIN)
    assert by_string.allowed_roles == by_enum.allowed_roles
    assert by_string.allowed_roles == frozenset({"ADMIN"})


def test_factory_projects_legacy_strings_onto_canonical():
    """verify_role("VIEWER") should accept canonical READ_ONLY."""
    dep = gateway_auth.verify_role("VIEWER")
    assert dep.allowed_roles == frozenset({"READ_ONLY"})


def test_allow_passes_through_when_role_canonical_matches():
    gateway_auth.token_validator = _StubValidator(
        {"role": "ADMIN", "tenant_id": "t", "sub": "u"},
    )
    dep = gateway_auth.verify_role(Role.ADMIN, Role.OWNER)
    claims = _run(dep, authorization="Bearer ok-token")
    assert claims["role_canonical"] == "ADMIN"


def test_allow_canonicalizes_legacy_role_before_checking():
    """Token says 'SECURITY' (legacy) — dep allows SECURITY_ANALYST."""
    gateway_auth.token_validator = _StubValidator(
        {"role": "SECURITY", "tenant_id": "t", "sub": "u"},
    )
    dep = gateway_auth.verify_role(Role.SECURITY_ANALYST)
    claims = _run(dep, authorization="Bearer ok-token")
    assert claims["role_canonical"] == "SECURITY_ANALYST"


def test_deny_on_role_mismatch_returns_403():
    gateway_auth.token_validator = _StubValidator(
        {"role": "DEVELOPER", "tenant_id": "t", "sub": "u"},
    )
    dep = gateway_auth.verify_role(Role.OWNER)
    with pytest.raises(HTTPException) as exc:
        _run(dep, authorization="Bearer ok-token")
    assert exc.value.status_code == 403
    assert "DEVELOPER" in exc.value.detail


def test_missing_header_returns_401_with_bearer_challenge():
    dep = gateway_auth.verify_role(Role.OWNER)
    with pytest.raises(HTTPException) as exc:
        _run(dep, authorization=None)
    assert exc.value.status_code == 401
    assert exc.value.headers["WWW-Authenticate"] == "Bearer"


def test_malformed_header_returns_401():
    dep = gateway_auth.verify_role(Role.OWNER)
    with pytest.raises(HTTPException) as exc:
        _run(dep, authorization="Token x")  # not Bearer
    assert exc.value.status_code == 401
    assert "Malformed" in exc.value.detail


def test_validator_failure_returns_401_with_detail():
    gateway_auth.token_validator = _StubValidator({"role": "OWNER"})
    dep = gateway_auth.verify_role(Role.OWNER)
    with pytest.raises(HTTPException) as exc:
        _run(dep, authorization="Bearer invalid")
    assert exc.value.status_code == 401
    assert "signature invalid" in exc.value.detail


def test_uninitialized_validator_returns_503():
    gateway_auth.token_validator = None
    dep = gateway_auth.verify_role(Role.OWNER)
    with pytest.raises(HTTPException) as exc:
        _run(dep, authorization="Bearer x")
    assert exc.value.status_code == 503


def test_unknown_role_in_token_falls_to_read_only_and_denies_when_not_listed():
    gateway_auth.token_validator = _StubValidator(
        {"role": "MYSTERY_ROLE", "tenant_id": "t", "sub": "u"},
    )
    dep = gateway_auth.verify_role(Role.OWNER, Role.ADMIN)
    with pytest.raises(HTTPException) as exc:
        _run(dep, authorization="Bearer ok-token")
    assert exc.value.status_code == 403


def test_unknown_role_in_token_falls_to_read_only_and_passes_when_listed():
    gateway_auth.token_validator = _StubValidator(
        {"role": "MYSTERY_ROLE", "tenant_id": "t", "sub": "u"},
    )
    dep = gateway_auth.verify_role(Role.READ_ONLY)
    claims = _run(dep, authorization="Bearer ok-token")
    assert claims["role_canonical"] == "READ_ONLY"
