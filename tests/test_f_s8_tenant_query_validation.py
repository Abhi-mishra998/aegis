"""Sprint EI-1.1 — F-S8 query-param validation guard.

Brutal-review 2026-06-19 flagged that `/audit/logs?tenant_id=X` and
`/incidents?tenant_id=X` silently ignored the parameter, returning JWT-tenant
data instead. That's not a data leak (JWT scope is enforced), but it's a
misleading contract.

reject_mismatched_tenant_query() turns the silent ignore into a loud 400 so a
developer who tried to query another tenant's data sees an error instead of
their own data.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.gateway._helpers import reject_mismatched_tenant_query


class _State:
    def __init__(self, tenant_id: str | None = None, jwt_claims: dict | None = None):
        self.tenant_id = tenant_id
        self.jwt_claims = jwt_claims
        self.actor = "test-actor"


class _URL:
    def __init__(self, path: str = "/audit/logs"):
        self.path = path


class _Req:
    """Minimal FastAPI Request stand-in — only the attributes the helper reads."""
    def __init__(self, query: dict[str, str] | None = None, tenant_id: str | None = None,
                 jwt_claims: dict | None = None, path: str = "/audit/logs"):
        self.query_params = query or {}
        self.state = _State(tenant_id=tenant_id, jwt_claims=jwt_claims)
        self.url = _URL(path)


def test_no_query_param_is_allowed() -> None:
    """Omitting tenant_id is the recommended pattern — must not error."""
    req = _Req(query={}, tenant_id="tnt-a")
    reject_mismatched_tenant_query(req)  # no raise


def test_matching_query_param_is_allowed() -> None:
    """Passing your own tenant_id is allowed (it's a no-op, but harmless)."""
    req = _Req(query={"tenant_id": "tnt-a"}, tenant_id="tnt-a")
    reject_mismatched_tenant_query(req)  # no raise


def test_mismatched_query_param_raises_400() -> None:
    """The F-S8 fix — passing another tenant's id yields 400, not silent data."""
    req = _Req(query={"tenant_id": "tnt-b"}, tenant_id="tnt-a")
    with pytest.raises(HTTPException) as exc:
        reject_mismatched_tenant_query(req)
    assert exc.value.status_code == 400
    assert "tenant_id" in exc.value.detail


def test_mismatched_when_only_jwt_claims_present_raises_400() -> None:
    """Some middleware paths populate jwt_claims but not request.state.tenant_id."""
    req = _Req(query={"tenant_id": "tnt-evil"}, tenant_id=None,
               jwt_claims={"tenant_id": "tnt-a"})
    with pytest.raises(HTTPException) as exc:
        reject_mismatched_tenant_query(req)
    assert exc.value.status_code == 400


def test_no_jwt_tenant_with_query_param_raises_400() -> None:
    """If we cannot prove ownership, never trust the query param."""
    req = _Req(query={"tenant_id": "tnt-a"}, tenant_id=None, jwt_claims=None)
    with pytest.raises(HTTPException) as exc:
        reject_mismatched_tenant_query(req)
    assert exc.value.status_code == 400


def test_empty_query_param_treated_as_mismatch() -> None:
    """Empty string is a non-None query value — fail loud."""
    req = _Req(query={"tenant_id": ""}, tenant_id="tnt-a")
    with pytest.raises(HTTPException) as exc:
        reject_mismatched_tenant_query(req)
    assert exc.value.status_code == 400
