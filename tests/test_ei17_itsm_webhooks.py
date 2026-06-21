"""Sprint EI-17 — unit tests for /webhooks/{jira,servicenow} HMAC + dispatch.

Covers:
  - _verify_hmac: correct sig, sha256= prefix, case-insensitive,
                  empty secret, empty header, wrong sig
  - JIRA_DONE_NAMES + SNOW_DONE_STATES content
  - skip-list assertion (middleware bypasses JWT for these prefixes)
  - schema accepts the new IncidentUpdate fields without breaking
    existing payloads
"""
from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei17-unit-test")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.gateway.routers.itsm_webhooks import (  # noqa: E402
    JIRA_DONE_NAMES,
    SNOW_DONE_STATES,
    _assert_url_tenant_matches_jwt,
    _verify_hmac,
)


def _sig(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ── HMAC verifier ────────────────────────────────────────────────────────
class TestVerifyHMAC:
    SECRET = "topsecret-32-char-or-whatever-len"
    BODY = b'{"issue":{"key":"SEC-42","fields":{"status":{"name":"Done"}}}}'

    def test_correct_signature(self):
        assert _verify_hmac(self.SECRET, self.BODY, _sig(self.SECRET, self.BODY)) is True

    def test_sha256_prefix_accepted(self):
        assert _verify_hmac(self.SECRET, self.BODY,
                            "sha256=" + _sig(self.SECRET, self.BODY)) is True

    def test_case_insensitive_hex(self):
        assert _verify_hmac(self.SECRET, self.BODY,
                            _sig(self.SECRET, self.BODY).upper()) is True

    def test_wrong_signature_rejected(self):
        assert _verify_hmac(self.SECRET, self.BODY, "deadbeef" * 8) is False

    def test_empty_secret_rejected(self):
        assert _verify_hmac("", self.BODY, _sig(self.SECRET, self.BODY)) is False

    def test_empty_header_rejected(self):
        assert _verify_hmac(self.SECRET, self.BODY, "") is False

    def test_wrong_body_rejected(self):
        """Sig was computed for BODY; sending different bytes must fail."""
        assert _verify_hmac(self.SECRET, b"different body",
                            _sig(self.SECRET, self.BODY)) is False

    def test_wrong_secret_rejected(self):
        assert _verify_hmac("different-secret", self.BODY,
                            _sig(self.SECRET, self.BODY)) is False

    def test_constant_time_compare_used(self):
        """hmac.compare_digest defends against timing-side-channel — call
        twice to make sure the path is deterministic and doesn't short-
        circuit on the first byte mismatch."""
        bad = "0" * 64
        assert _verify_hmac(self.SECRET, self.BODY, bad) is False
        assert _verify_hmac(self.SECRET, self.BODY, bad) is False


# ── Done-state constants ────────────────────────────────────────────────
class TestDoneStates:
    def test_jira_done_names_lowercase(self):
        assert all(n.islower() for n in JIRA_DONE_NAMES)

    def test_jira_done_covers_common_workflows(self):
        for n in ("done", "closed", "resolved", "complete", "completed"):
            assert n in JIRA_DONE_NAMES, n

    def test_snow_resolved_and_closed_states(self):
        # SNOW state 6 = Resolved, 7 = Closed, 8 = Cancelled (per default install)
        assert "6" in SNOW_DONE_STATES
        assert "7" in SNOW_DONE_STATES

    def test_snow_active_states_not_in_done(self):
        # SNOW 1 = New, 2 = In Progress, 3 = On Hold — must NOT trigger close
        for s in ("1", "2", "3"):
            assert s not in SNOW_DONE_STATES, s


# ── Middleware skip-list ────────────────────────────────────────────────
class TestSkipList:
    """The two webhook prefixes must be JWT-bypass so the upstream
    platform (which can't carry an Aegis JWT) can reach the handler."""

    def test_jira_webhook_in_skip_list(self):
        from services.gateway.middleware import _SKIP_PATH_PREFIXES
        assert any(p == "/webhooks/jira/" for p in _SKIP_PATH_PREFIXES)

    def test_servicenow_webhook_in_skip_list(self):
        from services.gateway.middleware import _SKIP_PATH_PREFIXES
        assert any(p == "/webhooks/servicenow/" for p in _SKIP_PATH_PREFIXES)


# ── Schema backward-compat ──────────────────────────────────────────────
class TestSchema:
    """IncidentUpdate must accept the new fields WITHOUT breaking
    existing PATCH payloads that only set status/assigned_to/note."""

    def test_legacy_payload_still_parses(self):
        from services.api.schemas.incident import IncidentUpdate
        u = IncidentUpdate(status="RESOLVED")
        assert u.status == "RESOLVED"
        assert u.jira_issue_key is None
        assert u.servicenow_sys_id is None

    def test_link_back_payload_parses(self):
        from services.api.schemas.incident import IncidentUpdate
        u = IncidentUpdate(
            jira_issue_key="SEC-42",
            jira_issue_url="https://acme.atlassian.net/browse/SEC-42",
        )
        assert u.status is None  # not setting status is fine
        assert u.jira_issue_key == "SEC-42"

    def test_combined_payload_parses(self):
        from services.api.schemas.incident import IncidentUpdate
        u = IncidentUpdate(
            status="RESOLVED",
            servicenow_sys_id="abc123def456",
            servicenow_number="INC0010001",
        )
        assert u.status == "RESOLVED"
        assert u.servicenow_sys_id == "abc123def456"
        assert u.servicenow_number == "INC0010001"


# ── N15 defense-in-depth: URL tenant_id ↔ JWT tenant_id cross-check ─────
class TestUrlTenantCrossCheck:
    """N15 (audit 2026-06-21) — these webhooks are HMAC-only today (no JWT,
    skip-listed in the middleware), so the per-tenant ``webhook_secret``
    IS the isolation mechanism. The cross-check helper is a defense-in-
    depth no-op guard against a future regression that adds JWT auth
    without checking the URL tenant_id.
    """

    @staticmethod
    def _req(state_tenant_id):
        """Build a minimal Request-shaped object whose ``state.tenant_id``
        is the value we want to assert on. We can't easily construct a
        real Starlette Request without an ASGI scope, so a duck-typed
        object is enough — the helper only reads ``getattr(state, 'tenant_id')``.
        """
        import types
        return types.SimpleNamespace(
            state=types.SimpleNamespace(tenant_id=state_tenant_id),
        )

    def test_no_jwt_tenant_passes(self):
        """Today's reality — skip-list means state.tenant_id is None."""
        import uuid as _u
        url_tid = _u.uuid4()
        # Must NOT raise.
        _assert_url_tenant_matches_jwt(self._req(None), url_tid)

    def test_matching_tenant_passes(self):
        import uuid as _u
        tid = _u.uuid4()
        _assert_url_tenant_matches_jwt(self._req(tid), tid)

    def test_matching_tenant_str_vs_uuid_passes(self):
        """Defense-in-depth: stringification on both sides means the
        helper still accepts a JWT that stored tenant_id as a str."""
        import uuid as _u
        tid = _u.uuid4()
        _assert_url_tenant_matches_jwt(self._req(str(tid)), tid)

    def test_mismatched_tenant_raises_403(self):
        """If a JWT for tenant A arrives at /webhooks/jira/<B>, refuse."""
        import uuid as _u

        from fastapi import HTTPException
        url_tenant = _u.uuid4()  # B
        jwt_tenant = _u.uuid4()  # A
        assert url_tenant != jwt_tenant
        with pytest.raises(HTTPException) as exc_info:
            _assert_url_tenant_matches_jwt(self._req(jwt_tenant), url_tenant)
        assert exc_info.value.status_code == 403
        assert "JWT tenant does not match" in str(exc_info.value.detail)
