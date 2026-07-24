"""Q32 regression: POST /analyze must return a clean 400-shape error
when `tokens` is non-numeric, not fall through to the record_action
`if tokens > 0` line and raise TypeError → uncaught 500.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_analyze_rejects_non_numeric_tokens():
    from services.behavior.main import analyze_behavior
    # verify_internal_secret is a FastAPI Depends — bypass by passing
    # the underlying function directly with _ set to a dummy value.
    with patch(
        "services.behavior.main.behavior_engine.record_action",
        new=AsyncMock(return_value={"ok": True}),
    ) as _mock:
        payload = {
            "tenant_id": str(uuid.uuid4()),
            "agent_id":  str(uuid.uuid4()),
            "tool":      "read",
            "tokens":    "not-a-number",
        }
        result = await analyze_behavior(payload, _="stub")
        assert result["success"] is False
        assert "invalid tokens" in result["error"]
        # record_action must NOT have been called — invalid input must
        # short-circuit at the endpoint boundary.
        _mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_analyze_accepts_int_string_tokens():
    """`int("100")` succeeds — the endpoint accepts numeric strings and
    coerces to int. That's a compat-friendly permissive parse."""
    from services.behavior.main import analyze_behavior
    with patch(
        "services.behavior.main.behavior_engine.record_action",
        new=AsyncMock(return_value={"ok": True}),
    ) as _mock:
        payload = {
            "tenant_id": str(uuid.uuid4()),
            "agent_id":  str(uuid.uuid4()),
            "tool":      "read",
            "tokens":    "100",
        }
        result = await analyze_behavior(payload, _="stub")
        assert result["success"] is True
        _mock.assert_awaited_once()
        # int coercion happened — record_action saw an int, not a string
        assert _mock.await_args.kwargs["tokens"] == 100


@pytest.mark.asyncio
async def test_analyze_defaults_tokens_to_zero_when_absent():
    from services.behavior.main import analyze_behavior
    with patch(
        "services.behavior.main.behavior_engine.record_action",
        new=AsyncMock(return_value={"ok": True}),
    ) as _mock:
        payload = {
            "tenant_id": str(uuid.uuid4()),
            "agent_id":  str(uuid.uuid4()),
            "tool":      "read",
        }
        result = await analyze_behavior(payload, _="stub")
        assert result["success"] is True
        assert _mock.await_args.kwargs["tokens"] == 0


@pytest.mark.asyncio
async def test_analyze_null_tokens_defaults_to_zero():
    """`{"tokens": null}` — the `payload.get("tokens", 0) or 0` fallback
    coerces None to 0. Prior bare int() on None would TypeError → 500."""
    from services.behavior.main import analyze_behavior
    with patch(
        "services.behavior.main.behavior_engine.record_action",
        new=AsyncMock(return_value={"ok": True}),
    ) as _mock:
        payload = {
            "tenant_id": str(uuid.uuid4()),
            "agent_id":  str(uuid.uuid4()),
            "tool":      "read",
            "tokens":    None,
        }
        result = await analyze_behavior(payload, _="stub")
        assert result["success"] is True
        assert _mock.await_args.kwargs["tokens"] == 0
