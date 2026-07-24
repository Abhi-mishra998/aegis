"""Real tests for the MCP-gate bearer token authenticator. Covers:
  * fail-CLOSED refusal to boot in production without a token
  * dev-mode ephemeral generation
  * constant-time comparison rejects wrong token
  * missing/malformed Authorization headers rejected with UNIFORM error
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import HTTPException


def _reload_router(monkeypatch, env: str | None, token: str | None):
    if env is not None:
        monkeypatch.setenv("ENVIRONMENT", env)
    if token is not None:
        monkeypatch.setenv("MCP_GATE_BEARER_TOKEN", token)
    else:
        monkeypatch.delenv("MCP_GATE_BEARER_TOKEN", raising=False)
    # Also clear the ephemeral fallback so re-init happens fresh.
    from services.mcp_gate import router as r
    importlib.reload(r)
    return r


class TestBootTime:
    def test_production_without_token_refuses_boot(self, monkeypatch):
        with pytest.raises(RuntimeError, match="MCP_GATE_BEARER_TOKEN must be set"):
            _reload_router(monkeypatch, env="production", token=None)

    def test_production_with_token_boots(self, monkeypatch):
        r = _reload_router(monkeypatch, env="production", token="prod-token-xxx")
        assert r._EXPECTED_BEARER == "prod-token-xxx"

    def test_dev_without_token_generates_ephemeral(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token=None)
        assert len(r._EXPECTED_BEARER) > 16  # url-safe base64 of 32 bytes

    def test_dev_with_token_uses_it(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token="dev-token")
        assert r._EXPECTED_BEARER == "dev-token"


class TestVerifyBearer:
    def test_missing_header_rejected(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token="secret-xyz")
        with pytest.raises(HTTPException) as exc:
            r.verify_mcp_bearer(authorization=None)
        assert exc.value.status_code == 401
        assert exc.value.detail == "Unauthorized"
        assert 'realm="aegis-mcp-gate"' in exc.value.headers["WWW-Authenticate"]

    def test_wrong_scheme_rejected(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token="secret-xyz")
        with pytest.raises(HTTPException) as exc:
            r.verify_mcp_bearer(authorization="Basic dXNlcjpwYXNz")
        assert exc.value.status_code == 401
        assert exc.value.detail == "Unauthorized"

    def test_empty_bearer_rejected(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token="secret-xyz")
        with pytest.raises(HTTPException) as exc:
            r.verify_mcp_bearer(authorization="Bearer ")
        assert exc.value.status_code == 401

    def test_wrong_bearer_rejected(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token="secret-xyz")
        with pytest.raises(HTTPException) as exc:
            r.verify_mcp_bearer(authorization="Bearer wrong-token")
        assert exc.value.status_code == 401
        assert exc.value.detail == "Unauthorized"

    def test_correct_bearer_accepted(self, monkeypatch):
        r = _reload_router(monkeypatch, env="development", token="secret-xyz")
        # No exception raised
        r.verify_mcp_bearer(authorization="Bearer secret-xyz")

    def test_error_shape_is_uniform_no_oracle(self, monkeypatch):
        """Every failure returns the same body + WWW-Authenticate.
        A caller can't distinguish "missing" from "wrong token" from
        the response — no oracle."""
        r = _reload_router(monkeypatch, env="development", token="secret-xyz")
        details = []
        for auth in (None, "Basic x", "Bearer ", "Bearer wrong"):
            try:
                r.verify_mcp_bearer(authorization=auth)
            except HTTPException as exc:
                details.append(
                    (exc.status_code, exc.detail, exc.headers.get("WWW-Authenticate"))
                )
        assert all(d == details[0] for d in details), details
