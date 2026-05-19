"""Contract tests for the /execute synchronous-response sprint (2026-05-15).

Verifies the four invariants the sprint promised:

* `SecurityMiddleware._escalate` now returns HTTP 403 (not 202) with the
  approval-required body shape — every caller of _escalate (decision
  ESCALATE branch + the autonomy `requires_approval` branch).
* `SecurityMiddleware._decision_timeout` exists and returns HTTP 504 with
  `error: "decision_timeout"`.
* `sdk/acp_client/client.Client._request` raises
  `EscalationRequiredError` when the gateway answers 403 + body
  `{"error": "approval_required"}`. Plain denial still raises the
  ordinary `DeniedError`.
* `sdk/acp_client/client.Client._request` raises `DecisionTimeoutError`
  when the gateway answers 504.

We mock the HTTP layer; no live gateway is needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

# --------------------------------------------------------------------------- #
# Gateway-side: middleware helpers                                            #
# --------------------------------------------------------------------------- #


def _make_middleware():
    from services.gateway.middleware import SecurityMiddleware
    fake_redis = MagicMock()
    # Bypass __init__ — only the response helpers are exercised here.
    instance = SecurityMiddleware.__new__(SecurityMiddleware)
    instance.redis = fake_redis
    return instance


class TestEscalateContract:
    def test_escalate_returns_403_not_202(self):
        instance = _make_middleware()
        resp = instance._escalate("needs approval")
        assert resp.status_code == 403, "escalate must no longer return 202"

    def test_escalate_body_carries_approval_required_error(self):
        import json
        instance = _make_middleware()
        body = json.loads(instance._escalate("needs approval").body.decode())
        assert body["success"] is False
        assert body["error"] == "approval_required"
        assert body["meta"]["code"] == 403
        # Category lets the SDK distinguish escalation from a plain deny.
        assert body["meta"]["category"] == "escalation"


class TestDecisionTimeoutContract:
    def test_decision_timeout_returns_504(self):
        instance = _make_middleware()
        resp = instance._decision_timeout("req-deadbeef")
        assert resp.status_code == 504

    def test_decision_timeout_body_shape(self):
        import json
        instance = _make_middleware()
        body = json.loads(instance._decision_timeout("req-deadbeef").body.decode())
        assert body["success"] is False
        assert body["error"] == "decision_timeout"
        assert body["meta"] == {
            "code": 504, "category": "timeout", "request_id": "req-deadbeef",
        }


# --------------------------------------------------------------------------- #
# Static check: no remaining 202 status_code in /execute paths                #
# --------------------------------------------------------------------------- #


def test_middleware_has_no_remaining_status_code_202():
    """Belt-and-braces grep: the dispatch pipeline must not embed status 202
    anywhere. If a future contributor adds one back, this test trips first."""
    src = open("services/gateway/middleware.py").read()
    # Allow the audit comment that explains the removal, but not an actual
    # `status_code=202` argument.
    forbidden_patterns = ("status_code=202", "status_code = 202", '"code": 202')
    for pat in forbidden_patterns:
        assert pat not in src, f"forbidden substring {pat!r} re-appeared"


def test_execute_openapi_responses_dict_omits_202():
    """The @app.post decorator on /execute must declare 200/4xx/5xx — no 202."""
    from services.gateway.main import _EXECUTE_RESPONSES
    assert 202 not in _EXECUTE_RESPONSES
    assert {200, 403, 429, 502, 504}.issubset(_EXECUTE_RESPONSES.keys())


# --------------------------------------------------------------------------- #
# SDK-side: error mapping                                                     #
# --------------------------------------------------------------------------- #


def _stub_response(status_code: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    import json
    req = httpx.Request("POST", "http://gateway/execute")
    return httpx.Response(
        status_code=status_code,
        request=req,
        headers=headers or {},
        content=json.dumps(body or {}).encode("utf-8"),
    )


def _patched_client(monkeypatch, response: httpx.Response):
    from sdk.acp_client import client as _client_mod
    instance = _client_mod.Client.__new__(_client_mod.Client)
    instance.api_key = "test"
    instance.base_url = "http://gateway"
    instance._http = MagicMock()
    instance._http.request = MagicMock(return_value=response)
    return instance


class TestSdkErrorMapping:
    def test_403_approval_required_raises_escalation_required_error(self, monkeypatch):
        from sdk.acp_client.errors import DeniedError, EscalationRequiredError
        resp = _stub_response(403, {
            "success": False,
            "error": "approval_required",
            "detail": "needs human approval",
            "meta": {"code": 403, "category": "escalation",
                     "request_id": "req-1", "contract_id": "ctr-9"},
        })
        instance = _patched_client(monkeypatch, resp)
        with pytest.raises(EscalationRequiredError) as exc:
            instance._request("POST", "/execute")
        # Subclass of DeniedError — existing catchers still see it.
        assert isinstance(exc.value, DeniedError)
        assert exc.value.reason == "approval_required"
        assert exc.value.contract_id == "ctr-9"
        assert exc.value.decision_id == "req-1"

    def test_403_other_denial_raises_plain_denied_error(self, monkeypatch):
        from sdk.acp_client.errors import DeniedError, EscalationRequiredError
        resp = _stub_response(403, {
            "success": False,
            "error": "Security Block: policy_violation",
            "detail": "tool not allowed",
            "meta": {"code": 403},
        })
        instance = _patched_client(monkeypatch, resp)
        with pytest.raises(DeniedError) as exc:
            instance._request("POST", "/execute")
        assert not isinstance(exc.value, EscalationRequiredError)
        assert "Security Block" in exc.value.reason

    def test_504_raises_decision_timeout_error(self, monkeypatch):
        from sdk.acp_client.errors import DecisionTimeoutError
        resp = _stub_response(504, {
            "success": False,
            "error": "decision_timeout",
            "detail": "Decision pipeline exceeded the gateway deadline.",
            "meta": {"code": 504, "category": "timeout", "request_id": "req-late"},
        })
        instance = _patched_client(monkeypatch, resp)
        with pytest.raises(DecisionTimeoutError) as exc:
            instance._request("POST", "/execute")
        assert exc.value.request_id == "req-late"
        assert "deadline" in exc.value.detail

    def test_429_still_raises_rate_limited(self, monkeypatch):
        """Regression: tightening 4xx handling must not change other codes."""
        from sdk.acp_client.errors import RateLimitedError
        resp = _stub_response(429, {"detail": "rate limit"}, headers={"Retry-After": "2"})
        instance = _patched_client(monkeypatch, resp)
        with pytest.raises(RateLimitedError) as exc:
            instance._request("POST", "/execute")
        assert exc.value.retry_after == 2.0

    def test_200_passes_through_unwrap(self, monkeypatch):
        """Happy path: 200 with APIResponse envelope returns the inner data."""
        resp = _stub_response(200, {"success": True, "data": {"action": "allow", "risk": 0.1}})
        instance = _patched_client(monkeypatch, resp)
        out = instance._request("POST", "/execute")
        assert out == {"action": "allow", "risk": 0.1}


# --------------------------------------------------------------------------- #
# Dispatch path: timeout exception inside outer try → 504, not 500            #
# --------------------------------------------------------------------------- #


def test_exception_branch_classifies_timeouts_as_504():
    """Read the source to confirm the explicit isinstance check for
    `(asyncio.TimeoutError, httpx.TimeoutException)` is in the generic
    except block. Static check — exercising the live dispatch needs the
    full middleware stack which is too heavy for a unit test."""
    src = open("services/gateway/middleware.py").read()
    assert "is_timeout = isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException))" in src
    assert "self._decision_timeout(request_id)" in src
    assert 'audit_reason = "decision_timeout"' in src
