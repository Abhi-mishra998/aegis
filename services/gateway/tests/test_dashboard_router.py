"""Unit tests — /dashboard/state must surface downstream failures.

U10 fix: the handler used to return `{"success": True, data: {...empty...}}`
for ANY downstream 5xx — operators saw "all good" while audit / billing /
insights were actually down. These tests pin the new operator-honest
behavior:

- All required downstreams dead → HTTP 503 with missing[]
- Some required downstreams dead → HTTP 200 with partial=True + missing[]
- All required downstreams alive → HTTP 200 with no `partial` flag
"""
from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Bootstrap minimum env so sdk.common.config.ACPSettings() can instantiate
# at import time. These values are never read by the dashboard handler —
# the handler reads *URL settings which we'd let default — but the
# settings module validates required fields up front.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/x")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "a" * 64)
os.environ.setdefault("INTERNAL_SECRET", "test-internal-secret")

import pytest  # noqa: E402

from fastapi.responses import JSONResponse  # noqa: E402

from services.gateway.routers import dashboard as dash  # noqa: E402


class _FakeResp:
    def __init__(self, status_code: int, body: dict | list | None = None,
                 raise_on_json: bool = False) -> None:
        self.status_code = status_code
        self._body = body if body is not None else {}
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._body


def _client_returning(mapping: dict[str, _FakeResp | Exception]):
    """Build a fake httpx-style client whose .get() dispatches by URL suffix."""

    async def _get(url, headers=None, params=None, timeout=None):
        for suffix, response in mapping.items():
            if url.endswith(suffix):
                if isinstance(response, Exception):
                    raise response
                return response
        # Default: 200 empty so unknown URLs don't accidentally fail tests.
        return _FakeResp(200, {})

    client = MagicMock()
    client.get = AsyncMock(side_effect=_get)
    return client


def _make_request(client, tenant_id: str = "") -> SimpleNamespace:
    """Minimal Request stand-in — the handler reads .app.state.client and
    a couple of headers, and internal_headers() also reads .cookies."""
    headers = {}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id

    def _hget(key, default=""):
        return headers.get(key, default)

    cookies = {}

    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(client=client)),
        headers=SimpleNamespace(get=_hget),
        cookies=cookies,
        state=SimpleNamespace(tenant_id=None, agent_id=None),
    )


# ---------------------------------------------------------------------------
# _safe_json — must report alive=False on every failure mode
# ---------------------------------------------------------------------------


def test_safe_json_returns_alive_true_on_200():
    client = _client_returning({"/x": _FakeResp(200, {"hello": "world"})})
    name, body, alive = asyncio.run(dash._safe_json("audit", client, "http://svc/x", {}))
    assert name == "audit"
    assert body == {"hello": "world"}
    assert alive is True


def test_safe_json_reports_dead_on_5xx():
    client = _client_returning({"/x": _FakeResp(503, {"oh": "no"})})
    name, body, alive = asyncio.run(dash._safe_json("audit", client, "http://svc/x", {}))
    assert alive is False
    assert body == {}


def test_safe_json_reports_dead_on_4xx():
    # 4xx is a real failure too — caller can't render KPIs from {}.
    client = _client_returning({"/x": _FakeResp(404)})
    _name, _body, alive = asyncio.run(dash._safe_json("audit", client, "http://svc/x", {}))
    assert alive is False


def test_safe_json_reports_dead_on_connection_error():
    client = _client_returning({"/x": ConnectionError("boom")})
    _name, _body, alive = asyncio.run(dash._safe_json("audit", client, "http://svc/x", {}))
    assert alive is False


def test_safe_json_reports_dead_on_unparseable_body():
    client = _client_returning({"/x": _FakeResp(200, raise_on_json=True)})
    _name, _body, alive = asyncio.run(dash._safe_json("audit", client, "http://svc/x", {}))
    assert alive is False


def test_safe_json_wraps_bare_list_in_data():
    client = _client_returning({"/x": _FakeResp(200, [1, 2, 3])})
    _name, body, alive = asyncio.run(dash._safe_json("ins", client, "http://svc/x", {}))
    assert alive is True
    assert body == {"data": [1, 2, 3]}


# ---------------------------------------------------------------------------
# dashboard_state — operator-honest aggregation
# ---------------------------------------------------------------------------


def test_dashboard_state_all_alive_returns_200_without_partial_flag():
    client = _client_returning({
        "/logs/summary":     _FakeResp(200, {"total": 42}),
        "/agents/summary":   _FakeResp(200, {"total": 10, "active": 8, "quarantined": 1, "high_risk": 2}),
        "/billing/summary":  _FakeResp(200, {"cost_usd": 12.34}),
        "/insights":         _FakeResp(200, []),
    })
    req = _make_request(client)
    body = asyncio.run(dash.dashboard_state(req))

    # Happy path returns a plain dict (FastAPI will JSON-encode it).
    assert isinstance(body, dict)
    assert body["success"] is True
    assert "partial" not in body
    assert "missing" not in body
    assert body["data"]["agents"]["total"] == 10
    assert body["data"]["audit"] == {"total": 42}


def test_dashboard_state_one_downstream_dead_returns_partial():
    # Audit is down; the other three are fine. Per Option B we return 200
    # with partial=True and missing=["audit"] so the UI can render a
    # banner above the still-live KPIs.
    client = _client_returning({
        "/logs/summary":     _FakeResp(503),
        "/agents/summary":   _FakeResp(200, {"total": 10}),
        "/billing/summary":  _FakeResp(200, {"cost_usd": 12.34}),
        "/insights":         _FakeResp(200, []),
    })
    req = _make_request(client)
    body = asyncio.run(dash.dashboard_state(req))

    assert isinstance(body, dict)
    assert body["success"] is True
    assert body.get("partial") is True
    assert body.get("missing") == ["audit"]
    # Live downstreams still surface their data.
    assert body["data"]["agents"]["total"] == 10
    assert body["data"]["billing"] == {"cost_usd": 12.34}
    # Dead audit becomes empty dict — UI banner explains why.
    assert body["data"]["audit"] == {}


def test_dashboard_state_two_downstreams_dead_lists_both_in_missing():
    client = _client_returning({
        "/logs/summary":     ConnectionError("audit gone"),
        "/agents/summary":   _FakeResp(200, {"total": 10}),
        "/billing/summary":  _FakeResp(503),
        "/insights":         _FakeResp(200, []),
    })
    req = _make_request(client)
    body = asyncio.run(dash.dashboard_state(req))

    assert body.get("partial") is True
    assert set(body.get("missing", [])) == {"audit", "billing"}


def test_dashboard_state_all_downstreams_dead_returns_503():
    # If every required downstream is dead the dashboard literally has
    # nothing to render. Lying with "success: True, data: {}" is the
    # exact failure mode U10 closes — return 503.
    client = _client_returning({
        "/logs/summary":     _FakeResp(503),
        "/agents/summary":   _FakeResp(503),
        "/billing/summary":  _FakeResp(503),
        "/insights":         _FakeResp(503),
    })
    req = _make_request(client)
    resp = asyncio.run(dash.dashboard_state(req))

    assert isinstance(resp, JSONResponse)
    assert resp.status_code == 503
    import json
    body = json.loads(resp.body)
    assert body["success"] is False
    assert body["error"] == "downstream_unavailable"
    assert set(body["missing"]) == {"audit", "agents", "billing", "insights"}


def test_dashboard_state_kill_switch_failure_does_not_count_toward_missing():
    # kill-switch is an OPTIONAL signal — its absence shouldn't trip the
    # partial banner because the dashboard can render KPIs without it.
    client = _client_returning({
        "/logs/summary":     _FakeResp(200, {"total": 1}),
        "/agents/summary":   _FakeResp(200, {"total": 1}),
        "/billing/summary":  _FakeResp(200, {}),
        "/insights":         _FakeResp(200, []),
        "/decision/kill-switch/t-1": _FakeResp(503),
    })
    req = _make_request(client, tenant_id="t-1")
    body = asyncio.run(dash.dashboard_state(req))

    assert isinstance(body, dict)
    assert "partial" not in body
    assert "missing" not in body
    assert body["data"]["kill_switch"] == {}


def test_dashboard_state_logs_warning_on_partial(caplog):
    import logging
    caplog.set_level(logging.WARNING, logger="gateway.dashboard")

    client = _client_returning({
        "/logs/summary":     _FakeResp(503),
        "/agents/summary":   _FakeResp(200, {"total": 1}),
        "/billing/summary":  _FakeResp(200, {}),
        "/insights":         _FakeResp(200, []),
    })
    req = _make_request(client)
    asyncio.run(dash.dashboard_state(req))

    # Two warnings expected: one from _safe_json (5xx) and one from the
    # handler (partial). Operator must see both.
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "dashboard.downstream_5xx" in msgs
    assert "dashboard.partial" in msgs
    assert "audit" in msgs
