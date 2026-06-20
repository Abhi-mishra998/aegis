"""Sprint EI-18 — unit tests for the webhook-secret rotate endpoints.

Covers the pieces testable without spinning up Postgres:
  - RBAC matrix (OWNER-only for rotate; the rest of /integrations/*
    inherits its existing rules)
  - _mint_webhook_secret format (64 hex chars = 32 bytes)
  - _webhook_base_url honours X-Forwarded-Host / X-Forwarded-Proto
  - _to_public_dict / _snow_to_public_dict surface has_webhook_secret
    without leaking the value
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei18-unit-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.gateway._rbac_map import is_authorized  # noqa: E402
from services.gateway.routers.integrations import (  # noqa: E402
    _mint_webhook_secret,
    _snow_to_public_dict,
    _to_public_dict,
    _webhook_base_url,
)


# ── _mint_webhook_secret ─────────────────────────────────────────────────
class TestMint:
    def test_format(self):
        s = _mint_webhook_secret()
        assert isinstance(s, str)
        assert len(s) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", s)

    def test_uniqueness(self):
        seen = {_mint_webhook_secret() for _ in range(200)}
        assert len(seen) == 200   # no collisions in 200 mints


# ── _webhook_base_url ────────────────────────────────────────────────────
class TestWebhookBaseUrl:
    def _req(self, headers: dict) -> SimpleNamespace:
        # Need a real-ish Request shape: .headers.get('X-Forwarded-…')
        from starlette.datastructures import Headers
        return SimpleNamespace(headers=Headers(headers))

    def test_uses_x_forwarded_host_when_present(self):
        tid = uuid.uuid4()
        req = self._req({"X-Forwarded-Host": "aegisagent.in",
                          "X-Forwarded-Proto": "https"})
        url = _webhook_base_url(req, "jira", tid)
        assert url == f"https://aegisagent.in/webhooks/jira/{tid}"

    def test_falls_back_to_host_header(self):
        tid = uuid.uuid4()
        req = self._req({"Host": "eu.aegisagent.in",
                          "X-Forwarded-Proto": "https"})
        url = _webhook_base_url(req, "servicenow", tid)
        assert url.startswith("https://eu.aegisagent.in/webhooks/servicenow/")
        assert str(tid) in url

    def test_falls_back_to_default_when_no_headers(self):
        tid = uuid.uuid4()
        req = self._req({})
        url = _webhook_base_url(req, "jira", tid)
        assert url == f"https://aegisagent.in/webhooks/jira/{tid}"

    def test_honours_http_when_x_forwarded_proto_is_http(self):
        """Local dev sometimes runs the gateway behind a plain-HTTP
        proxy — surface what the client actually sees."""
        tid = uuid.uuid4()
        req = self._req({"X-Forwarded-Host": "localhost:8000",
                          "X-Forwarded-Proto": "http"})
        url = _webhook_base_url(req, "jira", tid)
        assert url.startswith("http://localhost:8000/webhooks/jira/")


# ── _to_public_dict + _snow_to_public_dict surface has_webhook_secret ────
class TestPublicDict:
    def _jira_row(self, webhook_secret=None):
        from datetime import datetime, UTC
        return SimpleNamespace(
            id=uuid.uuid4(),
            base_url="https://acme.atlassian.net",
            project_key="SEC",
            account_email="bot@acme.com",
            api_token="secret-token",
            default_issue_type="Bug",
            default_priority="High",
            enabled=True,
            auto_create_on_incident=True,
            webhook_secret=webhook_secret,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def _snow_row(self, webhook_secret=None):
        from datetime import datetime, UTC
        return SimpleNamespace(
            id=uuid.uuid4(),
            instance_url="https://example.com",
            username="aegis_bot",
            password="topsecret",
            default_urgency=2,
            default_impact=2,
            default_category=None,
            default_assignment_group=None,
            enabled=True,
            auto_create_on_incident=True,
            webhook_secret=webhook_secret,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def test_jira_has_webhook_secret_false_when_unset(self):
        d = _to_public_dict(self._jira_row(webhook_secret=None))
        assert d["has_webhook_secret"] is False
        assert "webhook_secret" not in d   # never leak the value

    def test_jira_has_webhook_secret_true_when_set(self):
        d = _to_public_dict(self._jira_row(webhook_secret="x" * 64))
        assert d["has_webhook_secret"] is True
        assert "webhook_secret" not in d

    def test_snow_has_webhook_secret_false_when_unset(self):
        d = _snow_to_public_dict(self._snow_row(webhook_secret=None))
        assert d["has_webhook_secret"] is False
        assert "webhook_secret" not in d

    def test_snow_has_webhook_secret_true_when_set(self):
        d = _snow_to_public_dict(self._snow_row(webhook_secret="x" * 64))
        assert d["has_webhook_secret"] is True

    def test_jira_api_token_still_hidden(self):
        """The has_api_token guard from EI-2 must still hold."""
        d = _to_public_dict(self._jira_row())
        assert "api_token" not in d
        assert d["has_api_token"] is True


# ── RBAC matrix for the rotate endpoints ────────────────────────────────
class TestRBAC:
    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True),
        ("ADMIN", False),
        ("SECURITY_ANALYST", False),
        ("DEVELOPER", False),
        ("READ_ONLY", False),
    ])
    def test_jira_rotate_owner_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/jira/webhook-secret/rotate",
                              "POST", role)
        assert ok is allowed

    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True),
        ("ADMIN", False),
        ("DEVELOPER", False),
    ])
    def test_snow_rotate_owner_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/servicenow/webhook-secret/rotate",
                              "POST", role)
        assert ok is allowed

    def test_rotate_does_not_match_test_rule(self):
        """The /webhook-secret/rotate path must not accidentally match
        the more-permissive /jira/test pattern (which is OWNER+ADMIN)."""
        ok_admin, _ = is_authorized(
            "/integrations/jira/webhook-secret/rotate", "POST", "ADMIN",
        )
        # ADMIN was allowed for /test, must be DENIED for /rotate.
        assert ok_admin is False
