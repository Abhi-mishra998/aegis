"""Sprint EI-6 — unit tests for the ServiceNow ITSM pipeline.

Covers:
  - fire_servicenow():  missing-config skip, SSRF (loopback, link-local),
                        Basic auth header shape, body shape (urgency clamp,
                        impact clamp, correlation_id, assignment_group),
                        Atlassian-style 201 success, 401 error, network
                        exception, short_description 160-char cap
  - execute_step():     CREATE_SNOW_INCIDENT dispatch + param passthrough
  - severity mapper:    _severity_to_snow_levels CRITICAL/HIGH/MEDIUM/LOW
                        + unknown
  - RBAC matrix:        OWNER/ADMIN only for PUT/DELETE/test
"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
from typing import Any

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei6-unit-test")
os.environ.setdefault("ALERT_CRED_SOURCE", "env")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402

from services.autonomy.incident_watcher import _severity_to_snow_levels  # noqa: E402
from services.autonomy.webhook_executor import (  # noqa: E402
    execute_step,
    fire_servicenow,
)
from services.gateway._rbac_map import is_authorized  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


# ── 1. fire_servicenow contract ─────────────────────────────────────────
class TestFireServiceNow:
    def test_missing_config_returns_skipped(self):
        r = _run(fire_servicenow(short_description="x", instance_url="",
                                  username="", password=""))
        assert r["status"] == "skipped"
        assert "incomplete" in r["reason"]

    def test_ssrf_blocked_link_local(self):
        r = _run(fire_servicenow(short_description="x",
                                  instance_url="http://169.254.169.254",
                                  username="u", password="p"))
        assert r["status"] == "error"
        assert "blocked" in r["reason"]

    def test_ssrf_blocked_loopback(self):
        r = _run(fire_servicenow(short_description="x",
                                  instance_url="http://127.0.0.1:8080",
                                  username="u", password="p"))
        assert r["status"] == "error"

    def test_basic_auth_header_and_url(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["url"]     = url
                capture["headers"] = headers or {}
                capture["json"]    = json
                return httpx.Response(201, json={
                    "result": {"sys_id": "abc123def456", "number": "INC0010001"},
                })

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _Client)

        r = _run(fire_servicenow(
            short_description="t", instance_url="https://example.com",
            username="aegis_bot", password="SECRET",
        ))
        assert r["status"] == "created"
        assert r["sys_id"] == "abc123def456"
        assert r["number"] == "INC0010001"
        assert r["incident_url"] == "https://example.com/nav_to.do?uri=incident.do?sys_id=abc123def456"

        expected_auth = "Basic " + base64.b64encode(b"aegis_bot:SECRET").decode()
        assert capture["headers"]["Authorization"] == expected_auth
        assert capture["url"] == "https://example.com/api/now/table/incident"

    def test_body_shape_urgency_impact_correlation(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["json"] = json
                return httpx.Response(201, json={"result": {"sys_id": "s", "number": "INC1"}})

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _Client)

        _run(fire_servicenow(
            short_description="boom", instance_url="https://example.com",
            username="u", password="p", urgency=1, impact=2,
            assignment_group="g-sys-id", correlation_id="INC-aegis-42",
            category="security", context={"agent_id": "agt-42"},
        ))

        body = capture["json"]
        assert body["short_description"] == "boom"
        assert body["urgency"] == "1"
        assert body["impact"]  == "2"
        assert body["category"] == "security"
        assert body["assignment_group"] == "g-sys-id"
        assert body["correlation_id"]   == "INC-aegis-42"
        # context lines appended to description
        assert "Aegis context" in body["description"]
        assert "agent_id: agt-42" in body["description"]

    def test_urgency_impact_clamped_to_1_3(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["json"] = json
                return httpx.Response(201, json={"result": {"sys_id": "s", "number": "INC1"}})

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _Client)

        # Out-of-range urgency/impact → clamped to 1/3
        _run(fire_servicenow(short_description="x",
                              instance_url="https://example.com",
                              username="u", password="p",
                              urgency=99, impact=-5))
        assert capture["json"]["urgency"] == "3"   # clamped down from 99
        assert capture["json"]["impact"]  == "1"   # clamped up from -5

    def test_short_description_truncated_to_160(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _Client:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["json"] = json
                return httpx.Response(201, json={"result": {"sys_id": "s", "number": "INC1"}})

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _Client)

        long = "A" * 1000
        _run(fire_servicenow(short_description=long,
                              instance_url="https://example.com",
                              username="u", password="p"))
        assert len(capture["json"]["short_description"]) == 160

    def test_servicenow_error_returns_error(self, monkeypatch):
        class _ErrClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **k):
                # N26: SNOW echoes the username in its 401 body — must NOT
                # propagate to the API response.
                return httpx.Response(
                    401,
                    text='User Not Authenticated: invalid login for user "aegis_bot"',
                )

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _ErrClient)

        r = _run(fire_servicenow(short_description="t",
                                  instance_url="https://example.com",
                                  username="aegis_bot", password="bad"))
        assert r["status"] == "error"
        assert r["http_status"] == 401
        # N26 (2026-06-21): the caller-visible reason is a sanitised class,
        # NOT the upstream echo. Otherwise the test ticket endpoint leaks
        # SNOW's username back through Aegis's response body.
        assert r["reason"] == "ServiceNow auth failed"
        assert "aegis_bot" not in r["reason"]
        assert "User Not Authenticated" not in r["reason"]

    def test_network_exception_returns_error(self, monkeypatch):
        class _ExcClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **k):
                # N26: exception messages can carry proxy URLs (which may
                # embed creds). Must not leak.
                raise httpx.ConnectError(
                    "DNS down for proxy https://user:secret@proxy.local:8080"
                )

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _ExcClient)

        r = _run(fire_servicenow(short_description="t",
                                  instance_url="https://example.com",
                                  username="u", password="p"))
        assert r["status"] == "error"
        # N26: generic message — exception details stay in the structured log
        assert r["reason"] == "ServiceNow unavailable"
        assert "secret" not in r["reason"]
        assert "proxy.local" not in r["reason"]


# ── 2. execute_step dispatch ────────────────────────────────────────────
class TestDispatch:
    def test_create_snow_incident_routes_through(self):
        r = _run(execute_step({"action_type": "CREATE_SNOW_INCIDENT", "params": {}}))
        # No config → fire_servicenow skips
        assert r["status"] == "skipped"

    def test_create_snow_incident_param_passthrough(self, monkeypatch):
        captured: dict[str, Any] = {}

        async def _fake_fire(**kw):
            captured.update(kw)
            return {"status": "created", "number": "INC0099", "sys_id": "deadbeef"}

        monkeypatch.setattr("services.autonomy.webhook_executor.fire_servicenow",
                            _fake_fire)

        r = _run(execute_step(
            {
                "action_type": "CREATE_SNOW_INCIDENT",
                "params": {
                    "short_description": "boom",
                    "instance_url": "https://example.com",
                    "username": "aegis_bot",
                    "password": "TOK",
                    "urgency": 1,
                    "impact":  1,
                    "category": "security",
                },
            },
            context={"agent_id": "agt-1", "incident_id": "I42"},
        ))
        assert r["status"] == "created"
        assert captured["short_description"] == "boom"
        assert captured["instance_url"] == "https://example.com"
        assert captured["username"] == "aegis_bot"
        assert captured["password"] == "TOK"
        assert captured["urgency"] == 1
        assert captured["impact"]  == 1
        assert captured["category"] == "security"
        # incident_id from context becomes correlation_id for de-dupe
        assert captured["correlation_id"] == "I42"


# ── 3. Severity → SNOW urgency/impact mapper ────────────────────────────
class TestSeverityMapper:
    def test_critical_is_highest(self):
        assert _severity_to_snow_levels("CRITICAL") == (1, 1)

    def test_high(self):
        assert _severity_to_snow_levels("HIGH") == (1, 2)

    def test_medium(self):
        assert _severity_to_snow_levels("MEDIUM") == (2, 2)

    def test_low(self):
        assert _severity_to_snow_levels("LOW") == (3, 3)

    def test_unknown_defaults_to_medium(self):
        assert _severity_to_snow_levels("SOMETHING_ELSE") == (2, 2)

    def test_lowercase_is_normalized(self):
        # severity field can arrive lowercase from old events
        assert _severity_to_snow_levels("critical") == (1, 1)


# ── 4. RBAC matrix for /integrations/servicenow ─────────────────────────
class TestRBAC:
    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True), ("ADMIN", True),
        ("SECURITY_ANALYST", False), ("DEVELOPER", False), ("READ_ONLY", False),
    ])
    def test_put_snow_owner_admin_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/servicenow", "PUT", role)
        assert ok is allowed

    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True), ("ADMIN", True),
        ("SECURITY_ANALYST", False), ("DEVELOPER", False),
    ])
    def test_delete_snow_owner_admin_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/servicenow", "DELETE", role)
        assert ok is allowed

    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True), ("ADMIN", True),
        ("SECURITY_ANALYST", False), ("DEVELOPER", False),
    ])
    def test_post_snow_test_owner_admin_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/servicenow/test", "POST", role)
        assert ok is allowed

    @pytest.mark.parametrize("role", ["OWNER", "ADMIN", "SECURITY_ANALYST",
                                      "DEVELOPER", "READ_ONLY"])
    def test_get_snow_all_roles_can_read(self, role):
        ok, _ = is_authorized("/integrations/servicenow", "GET", role)
        assert ok is True
