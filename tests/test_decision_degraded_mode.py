"""Unit tests for the behavior-firewall degraded-mode sprint
(2026-05-15).

Covers:
* classify_behavior_result — ok/timeout/error/non-200/parse-error paths
* is_high_risk — tool, inference_risk, inference_flags
* apply_degraded_mode_policy — block_all / block_high_risk / allow_with_audit
  including: short-circuit Decision shape, extra flags, fall-through reasons,
  unknown-policy fallback, ok-status no-op
* `/evaluate` integration — patches httpx + Redis to verify
  - the behavior_firewall_decision audit row is emitted on every consult
  - service_status/latency_ms/policy_applied land in metadata
  - block_all + unreachable behavior → action=deny
  - allow_with_audit emits the degraded_mode_fail_open row
  - Prometheus consult counter increments under each label
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.decision.behavior_consult import (
    DEFAULT_DEGRADED_MODE_POLICY,
    apply_degraded_mode_policy,
    classify_behavior_result,
    is_high_risk,
)
from services.decision.schemas import ExecutionAction, OrchestrationRequest


# --------------------------------------------------------------------------- #
# classify_behavior_result                                                    #
# --------------------------------------------------------------------------- #


class TestClassifyBehaviorResult:
    def test_ok_response_extracts_data(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {"behavior_risk": 0.42, "anomaly_score": 0.1, "flags": ["seen_before"]},
        }
        status, data, score = classify_behavior_result(resp)
        assert status == "ok"
        assert score == pytest.approx(0.42)
        assert data["behavior_risk"] == pytest.approx(0.42)
        assert data["anomaly_score"] == pytest.approx(0.1)
        assert data["flags"] == ["seen_before"]

    def test_ok_response_with_empty_data_defaults(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.return_value = {"success": True, "data": {}}
        status, data, score = classify_behavior_result(resp)
        assert status == "ok"
        assert score == 0.0
        assert data["behavior_risk"] == 0.0
        assert data["flags"] == []

    def test_ok_response_with_bad_json_is_error(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json.side_effect = ValueError("not json")
        status, data, score = classify_behavior_result(resp)
        assert status == "error"
        assert score is None
        assert "behavior_service_unavailable" in data["flags"]

    def test_non_200_response_is_error(self):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 503
        status, data, score = classify_behavior_result(resp)
        assert status == "error"
        assert score is None
        assert data["behavior_risk"] == 0.5

    def test_asyncio_timeout_is_timeout(self):
        status, data, score = classify_behavior_result(asyncio.TimeoutError())
        assert status == "timeout"
        assert score is None
        assert "behavior_service_unavailable" in data["flags"]

    def test_httpx_timeout_is_timeout(self):
        status, data, score = classify_behavior_result(httpx.ConnectTimeout("slow"))
        assert status == "timeout"
        assert score is None

    def test_fanout_timed_out_flag_overrides_value(self):
        status, _, _ = classify_behavior_result(None, fanout_timed_out=True)
        assert status == "timeout"

    def test_generic_exception_is_error(self):
        status, data, score = classify_behavior_result(RuntimeError("boom"))
        assert status == "error"
        assert score is None
        assert "behavior_service_unavailable" in data["flags"]

    def test_unknown_shape_defaults_to_error(self):
        # asyncio.gather can theoretically return None if a coroutine yielded
        # None instead of raising — never trust the slot blindly.
        status, _, _ = classify_behavior_result(None)
        assert status == "error"


# --------------------------------------------------------------------------- #
# is_high_risk                                                                #
# --------------------------------------------------------------------------- #


class TestIsHighRisk:
    @pytest.mark.parametrize("tool", ["exec", "shell", "query", "write_file", "delete", "kill"])
    def test_high_risk_tool_names(self, tool):
        assert is_high_risk(tool, 0.0, []) is True

    def test_tool_match_is_case_insensitive(self):
        assert is_high_risk("EXEC", 0.0, []) is True
        assert is_high_risk("  Shell  ", 0.0, []) is True

    def test_low_risk_tool_alone_is_not_high(self):
        assert is_high_risk("read_file", 0.0, []) is False
        assert is_high_risk("list_dir", 0.1, []) is False

    def test_high_inference_risk_floor(self):
        assert is_high_risk("read_file", 0.50, []) is True
        assert is_high_risk("read_file", 0.49, []) is False

    def test_high_risk_inference_flags(self):
        assert is_high_risk("read_file", 0.0, ["SENSITIVE_PATH_DETECTED"]) is True
        assert is_high_risk("read_file", 0.0, ["SQL_INJECTION_PATTERN"]) is True
        assert is_high_risk("read_file", 0.0, ["UNRELATED_FLAG"]) is False

    def test_handles_none_inputs(self):
        assert is_high_risk("read_file", 0.0, None) is False
        assert is_high_risk("", 0.0, None) is False


# --------------------------------------------------------------------------- #
# apply_degraded_mode_policy                                                  #
# --------------------------------------------------------------------------- #


class TestApplyDegradedModePolicy:
    def _data(self):
        return {"behavior_risk": 0.5, "anomaly_score": 0.5,
                "flags": ["behavior_service_unavailable"]}

    def test_ok_status_is_noop(self):
        out = apply_degraded_mode_policy(
            "block_all", tool="exec", inference_risk=0.0, inference_flags=[],
            behavior_data=self._data(), service_status="ok",
        )
        assert out.short_circuit is None
        assert out.policy_applied == "behavior_consulted"
        assert out.extra_reasons == []
        assert out.emit_fail_open_audit is False

    def test_block_all_denies_low_risk_too(self):
        out = apply_degraded_mode_policy(
            "block_all", tool="read_file", inference_risk=0.0, inference_flags=[],
            behavior_data=self._data(), service_status="timeout",
        )
        assert out.short_circuit is not None
        assert out.short_circuit.action == ExecutionAction.DENY
        assert "behavior_degraded_blocked" in out.short_circuit.reasons
        assert out.policy_applied == "block_all"

    def test_block_high_risk_denies_high_risk_tool(self):
        out = apply_degraded_mode_policy(
            "block_high_risk", tool="exec", inference_risk=0.0, inference_flags=[],
            behavior_data=self._data(), service_status="error",
        )
        assert out.short_circuit is not None
        assert out.short_circuit.action == ExecutionAction.DENY
        assert "behavior_degraded_blocked" in out.short_circuit.reasons

    def test_block_high_risk_allows_low_risk_with_flag(self):
        out = apply_degraded_mode_policy(
            "block_high_risk", tool="read_file", inference_risk=0.1, inference_flags=[],
            behavior_data=self._data(), service_status="timeout",
        )
        assert out.short_circuit is None
        assert "behavior_degraded_low_risk_allowed" in out.behavior_data["flags"]
        assert out.extra_reasons == ["behavior_degraded_low_risk_allowed"]

    def test_block_high_risk_inference_floor_triggers_block(self):
        # inference_risk >= 0.50 alone marks the call high-risk even if the
        # tool itself looks innocent.
        out = apply_degraded_mode_policy(
            "block_high_risk", tool="read_file", inference_risk=0.55,
            inference_flags=[], behavior_data=self._data(), service_status="error",
        )
        assert out.short_circuit is not None

    def test_allow_with_audit_falls_through_with_flag(self):
        out = apply_degraded_mode_policy(
            "allow_with_audit", tool="exec", inference_risk=0.9, inference_flags=[],
            behavior_data=self._data(), service_status="timeout",
        )
        assert out.short_circuit is None
        assert "behavior_degraded_fail_open" in out.behavior_data["flags"]
        assert out.emit_fail_open_audit is True
        assert out.extra_reasons == ["behavior_degraded_fail_open"]

    def test_unknown_policy_falls_back_to_default(self):
        # Default is block_high_risk → high-risk tool is denied.
        out = apply_degraded_mode_policy(
            "wat", tool="exec", inference_risk=0.0, inference_flags=[],
            behavior_data=self._data(), service_status="timeout",
        )
        assert out.short_circuit is not None
        assert out.policy_applied == DEFAULT_DEGRADED_MODE_POLICY

    def test_none_policy_falls_back_to_default(self):
        out = apply_degraded_mode_policy(
            None, tool="read_file", inference_risk=0.0, inference_flags=[],
            behavior_data=self._data(), service_status="timeout",
        )
        assert out.short_circuit is None
        assert out.policy_applied == DEFAULT_DEGRADED_MODE_POLICY


# --------------------------------------------------------------------------- #
# /evaluate integration with patched httpx + Redis                            #
# --------------------------------------------------------------------------- #


@pytest.fixture
def patched_evaluate(monkeypatch):
    """Yield the unbound /evaluate handler with `_http_client` + module-level
    `redis` patched so we can drive every consult outcome without standing up
    a real network or Redis instance.
    """
    from services.decision import main as decision_main

    captured: dict[str, list[dict[str, Any]]] = {"audit_events": []}

    # Patch redis with an AsyncMock so push_audit_event can call xadd freely.
    fake_redis = AsyncMock()
    fake_redis.xadd = AsyncMock(return_value=b"1-0")

    async def _capture_push_audit_event(redis, **kwargs):
        captured["audit_events"].append(kwargs)
        return None

    monkeypatch.setattr(decision_main, "redis", fake_redis)
    monkeypatch.setattr(decision_main, "push_audit_event", _capture_push_audit_event)

    # Stub the policy + behavior HTTP client. By default policy says "allow",
    # tests override `behavior_response` to drive each outcome.
    behavior_response_holder: dict[str, Any] = {"value": None, "exc": None}

    class _FakeClient:
        async def post(self, url, json=None, headers=None, timeout=None):
            if "policy" in url:
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = 200
                resp.json = MagicMock(return_value={"data": {"allowed": True, "risk_adjustment": 0.0}})
                return resp
            # behavior endpoint
            if behavior_response_holder["exc"] is not None:
                raise behavior_response_holder["exc"]
            return behavior_response_holder["value"]

        async def get(self, *args, **kwargs):  # registry lookups
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.json = MagicMock(return_value={"success": True, "data": {"status": "active", "permissions": []}})
            return resp

    monkeypatch.setattr(decision_main, "_http_client", _FakeClient())

    # Disable the Groq AI override so it can't mutate the decision.
    monkeypatch.setattr(decision_main, "groq_brain", None)

    return decision_main, captured, behavior_response_holder


def _make_request(tool: str = "read_file", metadata: dict | None = None, inference_risk: float = 0.1) -> OrchestrationRequest:
    return OrchestrationRequest(
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool=tool,
        tokens=10,
        inference_risk=inference_risk,
        inference_flags=[],
        request_id=str(uuid.uuid4()),
        payload_hash="",
        client_ip="127.0.0.1",
        metadata=metadata or {},
    )


def _ok_behavior_response(behavior_risk: float = 0.1) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json = MagicMock(return_value={
        "success": True,
        "data": {"behavior_risk": behavior_risk, "anomaly_score": 0.0, "flags": []},
    })
    return resp


@pytest.mark.asyncio
async def test_evaluate_emits_audit_on_healthy_consult(patched_evaluate):
    decision_main, captured, behavior_holder = patched_evaluate
    behavior_holder["value"] = _ok_behavior_response(behavior_risk=0.15)

    req = _make_request(tool="read_file", metadata={"degraded_mode_policy": "block_high_risk"})
    decision = await decision_main.evaluate_decision(req, _="ok")

    actions = [e["action"] for e in captured["audit_events"]]
    assert "behavior_firewall_decision" in actions
    bfd = next(e for e in captured["audit_events"] if e["action"] == "behavior_firewall_decision")
    assert bfd["metadata"]["service_status"] == "ok"
    assert bfd["metadata"]["policy_applied"] == "behavior_consulted"
    assert bfd["metadata"]["returned_score"] == pytest.approx(0.15)
    assert bfd["metadata"]["latency_ms"] >= 0
    # Healthy consult should NOT emit degraded_mode_fail_open.
    assert "degraded_mode_fail_open" not in actions
    # Engine still produced an outcome (allow expected on low risk).
    assert decision.action.value == "allow"


@pytest.mark.asyncio
async def test_evaluate_block_all_denies_when_behavior_unreachable(patched_evaluate):
    decision_main, captured, behavior_holder = patched_evaluate
    behavior_holder["exc"] = httpx.ConnectError("connection refused")

    req = _make_request(
        tool="read_file",
        metadata={"degraded_mode_policy": "block_all"},
    )
    decision = await decision_main.evaluate_decision(req, _="ok")

    assert decision.action.value == "deny"
    assert "behavior_degraded_blocked" in decision.reasons

    bfd = next(e for e in captured["audit_events"] if e["action"] == "behavior_firewall_decision")
    assert bfd["metadata"]["service_status"] == "error"
    assert bfd["metadata"]["policy_applied"] == "block_all"
    assert bfd["decision"] == "deny"


@pytest.mark.asyncio
async def test_evaluate_block_high_risk_allows_low_risk_with_reason(patched_evaluate):
    decision_main, captured, behavior_holder = patched_evaluate
    behavior_holder["exc"] = httpx.ConnectError("down")

    req = _make_request(
        tool="read_file",
        inference_risk=0.0,
        metadata={"degraded_mode_policy": "block_high_risk"},
    )
    decision = await decision_main.evaluate_decision(req, _="ok")

    # Low-risk path still allows but stamps the reason.
    assert decision.action.value != "deny"
    assert "behavior_degraded_low_risk_allowed" in decision.reasons

    bfd = next(e for e in captured["audit_events"] if e["action"] == "behavior_firewall_decision")
    assert bfd["metadata"]["policy_applied"] == "block_high_risk"
    assert bfd["metadata"]["service_status"] == "error"


@pytest.mark.asyncio
async def test_evaluate_block_high_risk_denies_high_risk_tool(patched_evaluate):
    decision_main, captured, behavior_holder = patched_evaluate
    behavior_holder["exc"] = asyncio.TimeoutError()

    req = _make_request(
        tool="exec",
        metadata={"degraded_mode_policy": "block_high_risk"},
    )
    decision = await decision_main.evaluate_decision(req, _="ok")

    assert decision.action.value == "deny"
    assert "behavior_degraded_blocked" in decision.reasons

    bfd = next(e for e in captured["audit_events"] if e["action"] == "behavior_firewall_decision")
    assert bfd["metadata"]["service_status"] == "timeout"
    assert bfd["metadata"]["policy_applied"] == "block_high_risk"


@pytest.mark.asyncio
async def test_evaluate_allow_with_audit_emits_extra_row(patched_evaluate):
    decision_main, captured, behavior_holder = patched_evaluate
    behavior_holder["exc"] = httpx.ConnectError("down")

    req = _make_request(
        tool="exec",  # would normally be denied under default policy
        metadata={"degraded_mode_policy": "allow_with_audit"},
    )
    decision = await decision_main.evaluate_decision(req, _="ok")

    # The fail-open posture means we still go through the engine — confirm we
    # at least see the reason in the response so it isn't silent.
    assert "behavior_degraded_fail_open" in decision.reasons

    actions = [e["action"] for e in captured["audit_events"]]
    assert "behavior_firewall_decision" in actions
    assert "degraded_mode_fail_open" in actions


@pytest.mark.asyncio
async def test_evaluate_increments_prometheus_consult_counter(patched_evaluate):
    decision_main, _captured, behavior_holder = patched_evaluate

    from sdk.utils import BEHAVIOR_FIREWALL_CONSULT_TOTAL

    def _val(label: str) -> float:
        return BEHAVIOR_FIREWALL_CONSULT_TOTAL.labels(result=label)._value.get()

    before_ok = _val("ok")
    before_err = _val("error")

    # ok path
    behavior_holder["exc"] = None
    behavior_holder["value"] = _ok_behavior_response()
    await decision_main.evaluate_decision(_make_request(metadata={}), _="ok")

    # error path
    behavior_holder["exc"] = httpx.ConnectError("down")
    behavior_holder["value"] = None
    await decision_main.evaluate_decision(_make_request(metadata={"degraded_mode_policy": "allow_with_audit"}), _="ok")

    assert _val("ok") >= before_ok + 1
    assert _val("error") >= before_err + 1


@pytest.mark.asyncio
async def test_evaluate_audit_metadata_carries_request_id(patched_evaluate):
    decision_main, captured, behavior_holder = patched_evaluate
    behavior_holder["value"] = _ok_behavior_response()

    req_id = "req-deadbeef-0001"
    req = _make_request(metadata={})
    req = req.model_copy(update={"request_id": req_id})
    await decision_main.evaluate_decision(req, _="ok")

    bfd = next(e for e in captured["audit_events"] if e["action"] == "behavior_firewall_decision")
    assert bfd["request_id"] == req_id
    assert bfd["metadata"]["request_id"] == req_id
