"""Sprint EI-2 — unit tests for the Jira ITSM pipeline.

Covers:
  - fire_jira()       — SSRF guard, missing-config skip, ADF body shape,
                        success / error parsing, dedup-style header set
  - execute_step()    — CREATE_JIRA_ISSUE dispatch routes through fire_jira
  - RBAC matrix       — only OWNER / ADMIN can mutate /integrations/jira

The Atlassian endpoint itself is mocked via httpx's MockTransport; we
never hit the network during the test run.
"""
from __future__ import annotations

import asyncio
import os
import sys
import base64
from typing import Any

import pytest

# webhook_executor requires INTERNAL_SECRET at import time — set it before
# any "from services.autonomy..." import to avoid KeyError during collect.
os.environ.setdefault("INTERNAL_SECRET", "ei2-unit-test")
os.environ.setdefault("ALERT_CRED_SOURCE", "env")

# Make the project importable when pytest is invoked from anywhere.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import httpx  # noqa: E402

from services.autonomy.webhook_executor import (  # noqa: E402
    _adf_paragraph,
    execute_step,
    fire_jira,
)
from services.gateway._rbac_map import is_authorized  # noqa: E402


# ── Helpers ──────────────────────────────────────────────────────────────
def _run(coro):
    """Python 3.14 deprecated get_event_loop() outside of running loops.
    asyncio.run() is the supported entrypoint and creates+closes its own loop."""
    return asyncio.run(coro)


def _make_mock_transport(*, status: int = 201, body: dict | None = None,
                         capture: dict | None = None):
    """Build an httpx MockTransport that records each request into capture."""
    def handler(req: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["method"]  = req.method
            capture["url"]     = str(req.url)
            capture["headers"] = dict(req.headers)
            capture["body"]    = req.content.decode() if req.content else ""
        return httpx.Response(status, json=body or {"id": "10001", "key": "SEC-42"})
    return httpx.MockTransport(handler)


# ── 1. fire_jira contract ───────────────────────────────────────────────
class TestFireJira:
    def test_missing_config_returns_skipped(self):
        r = _run(fire_jira(summary="x", base_url="", account_email="",
                           api_token="", project_key=""))
        assert r["status"] == "skipped"
        assert "incomplete" in r["reason"]

    def test_ssrf_blocked_link_local(self):
        r = _run(fire_jira(summary="x",
                           base_url="http://169.254.169.254",
                           account_email="a@b.com",
                           api_token="tok", project_key="P"))
        assert r["status"] == "error"
        assert "blocked" in r["reason"]

    def test_ssrf_blocked_loopback(self):
        r = _run(fire_jira(summary="x",
                           base_url="http://127.0.0.1:8080",
                           account_email="a@b.com",
                           api_token="tok", project_key="P"))
        assert r["status"] == "error"

    def test_basic_auth_header_correct(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _CapAsyncClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["url"]     = url
                capture["headers"] = headers or {}
                capture["json"]    = json
                return httpx.Response(201, json={"id": "10001", "key": "SEC-42"})

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _CapAsyncClient)

        r = _run(fire_jira(
            summary="t", base_url="https://acme.atlassian.net",
            account_email="bot@acme.com", api_token="ATOK", project_key="SEC",
        ))
        assert r["status"] == "created"
        assert r["issue_key"] == "SEC-42"
        assert r["issue_url"] == "https://acme.atlassian.net/browse/SEC-42"

        expected_auth = "Basic " + base64.b64encode(b"bot@acme.com:ATOK").decode()
        assert capture["headers"]["Authorization"] == expected_auth
        assert capture["headers"]["Accept"] == "application/json"
        assert capture["headers"]["Content-Type"] == "application/json"
        assert capture["url"] == "https://acme.atlassian.net/rest/api/3/issue"

    def test_body_shape_adf(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _CapAsyncClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["json"] = json
                return httpx.Response(201, json={"id": "1", "key": "X-1"})

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _CapAsyncClient)

        _run(fire_jira(summary="boom", base_url="https://acme.atlassian.net",
                       account_email="a@b.com", api_token="t", project_key="P",
                       description="Detected a thing.", priority="High",
                       labels=["aegis", "sev-high"], context={"agent": "agt-42"}))

        fields = capture["json"]["fields"]
        assert fields["project"]   == {"key": "P"}
        assert fields["summary"]   == "boom"
        assert fields["issuetype"] == {"name": "Bug"}
        assert fields["priority"]  == {"name": "High"}
        assert fields["labels"]    == ["aegis", "sev-high"]
        adf = fields["description"]
        assert adf["type"] == "doc" and adf["version"] == 1
        assert adf["content"][0]["content"][0]["text"] == "Detected a thing."
        # context bullet list
        assert adf["content"][1]["type"] == "bulletList"

    def test_atlassian_error_returns_error(self, monkeypatch):
        class _ErrClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **k):
                return httpx.Response(401, text="Unauthorized")

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _ErrClient)

        r = _run(fire_jira(summary="t",
                           base_url="https://acme.atlassian.net",
                           account_email="a@b.com", api_token="bad",
                           project_key="P"))
        assert r["status"] == "error"
        assert r["http_status"] == 401

    def test_network_exception_returns_error(self, monkeypatch):
        class _ExcClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **k):
                raise httpx.ConnectError("DNS down")

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _ExcClient)

        r = _run(fire_jira(summary="t",
                           base_url="https://acme.atlassian.net",
                           account_email="a@b.com", api_token="t",
                           project_key="P"))
        assert r["status"] == "error"
        assert "DNS down" in r["reason"]

    def test_summary_truncated_to_255(self, monkeypatch):
        capture: dict[str, Any] = {}

        class _CapClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, url, json=None, headers=None):
                capture["json"] = json
                return httpx.Response(201, json={"id": "1", "key": "X-1"})

        monkeypatch.setattr("services.autonomy.webhook_executor.httpx.AsyncClient",
                            _CapClient)

        long = "A" * 1000
        _run(fire_jira(summary=long, base_url="https://acme.atlassian.net",
                       account_email="a@b.com", api_token="t", project_key="P"))
        assert len(capture["json"]["fields"]["summary"]) == 255


# ── 2. ADF helper ────────────────────────────────────────────────────────
class TestADF:
    def test_plain_paragraph(self):
        d = _adf_paragraph("hello")
        assert d["type"] == "doc"
        assert d["content"][0]["content"][0]["text"] == "hello"
        assert len(d["content"]) == 1  # no bulletList when context is None

    def test_with_context(self):
        d = _adf_paragraph("hi", context={"a": "1", "b": "2"})
        assert d["content"][1]["type"] == "bulletList"
        items = d["content"][1]["content"]
        assert len(items) == 2
        assert "a: 1" in items[0]["content"][0]["content"][0]["text"]

    def test_context_capped_at_10(self):
        ctx = {f"k{i}": str(i) for i in range(50)}
        d = _adf_paragraph("hi", context=ctx)
        assert len(d["content"][1]["content"]) == 10


# ── 3. execute_step dispatch ────────────────────────────────────────────
class TestDispatch:
    def test_create_jira_issue_routes_through(self):
        r = _run(execute_step({"action_type": "CREATE_JIRA_ISSUE", "params": {}}))
        # No config → fire_jira skips
        assert r["status"] == "skipped"

    def test_create_jira_issue_param_passthrough(self, monkeypatch):
        captured: dict[str, Any] = {}

        async def _fake_fire_jira(**kw):
            captured.update(kw)
            return {"status": "created", "issue_key": "SEC-99"}

        monkeypatch.setattr("services.autonomy.webhook_executor.fire_jira",
                            _fake_fire_jira)

        r = _run(execute_step(
            {
                "action_type": "CREATE_JIRA_ISSUE",
                "params": {
                    "summary": "specific summary",
                    "base_url": "https://acme.atlassian.net",
                    "account_email": "bot@acme.com",
                    "api_token": "TOK",
                    "project_key": "SEC",
                    "issue_type": "Task",
                    "priority": "Highest",
                    "labels": ["aegis"],
                },
            },
            context={"agent_id": "agt-1"},
        ))
        assert r["status"] == "created"
        assert captured["summary"] == "specific summary"
        assert captured["project_key"] == "SEC"
        assert captured["issue_type"] == "Task"
        assert captured["priority"] == "Highest"


# ── 4. RBAC matrix for /integrations/jira ───────────────────────────────
class TestRBAC:
    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True), ("ADMIN", True),
        ("SECURITY_ANALYST", False), ("DEVELOPER", False), ("READ_ONLY", False),
    ])
    def test_put_jira_owner_admin_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/jira", "PUT", role)
        assert ok is allowed

    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True), ("ADMIN", True),
        ("SECURITY_ANALYST", False), ("DEVELOPER", False),
    ])
    def test_delete_jira_owner_admin_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/jira", "DELETE", role)
        assert ok is allowed

    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True), ("ADMIN", True),
        ("SECURITY_ANALYST", False), ("DEVELOPER", False),
    ])
    def test_post_jira_test_owner_admin_only(self, role, allowed):
        ok, _ = is_authorized("/integrations/jira/test", "POST", role)
        assert ok is allowed

    @pytest.mark.parametrize("role", ["OWNER", "ADMIN", "SECURITY_ANALYST",
                                      "DEVELOPER", "READ_ONLY"])
    def test_get_jira_all_roles_can_read(self, role):
        # Reading the config (with token redacted) is min READ_ONLY.
        ok, _ = is_authorized("/integrations/jira", "GET", role)
        assert ok is True
