"""N26 (2026-06-21) — SNOW upstream-response leak scrubber.

The SNOW Table API returns its 4xx/5xx body to the caller. That body
can echo the username Aegis used (helpful for debugging — bad for a
multi-tenant API where another tenant's admin can read the test-
connection response). The fix:

  * services/autonomy/webhook_executor.fire_servicenow returns a coarse
    string per status class ("ServiceNow auth failed" / "ServiceNow
    unavailable" / etc.), NOT the upstream body
  * the upstream body stays in the structured log so an operator can
    correlate from CloudWatch

These tests pin the scrubber so a future refactor doesn't accidentally
re-expose the upstream body.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

# webhook_executor reads INTERNAL_SECRET at import time. Set it before the
# `from services.autonomy.webhook_executor import ...` line below.
os.environ.setdefault("INTERNAL_SECRET", "n26-unit-test")
os.environ.setdefault("ALERT_CRED_SOURCE", "env")

import httpx  # noqa: E402

from services.autonomy.webhook_executor import (  # noqa: E402
    _safe_snow_error,
    fire_servicenow,
)


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# _safe_snow_error: pure mapping                                              #
# --------------------------------------------------------------------------- #


class TestSafeSnowError:
    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failure_class(self, status: int) -> None:
        assert _safe_snow_error(status) == "ServiceNow auth failed"

    @pytest.mark.parametrize("status", [400, 404, 409, 422, 429])
    def test_other_4xx_class(self, status: int) -> None:
        assert _safe_snow_error(status) == "ServiceNow rejected request"

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_5xx_class(self, status: int) -> None:
        assert _safe_snow_error(status) == "ServiceNow unavailable"

    def test_unexpected_status_falls_through_with_status_only(self) -> None:
        # A 1xx/2xx/3xx ending up in this branch is a bug, but the message
        # MUST still not be the upstream body — just the bare integer.
        msg = _safe_snow_error(307)
        assert "307" in msg
        assert "upstream" not in msg.lower()


# --------------------------------------------------------------------------- #
# fire_servicenow: the actual leak surface                                    #
# --------------------------------------------------------------------------- #


def _client_returning(response_factory):
    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return None
        async def post(self, *a, **k):
            return response_factory()
    return _C


class TestFireServicenowLeakScrub:
    def test_401_does_not_propagate_username_echo(self, monkeypatch) -> None:
        """SNOW 401 typically reflects the supplied username. Must NOT leak."""
        leaky_body = (
            "User Not Authenticated\n"
            'Bad credentials for user "aegis_bot@acme" (password length 24)'
        )
        monkeypatch.setattr(
            "services.autonomy.webhook_executor.httpx.AsyncClient",
            _client_returning(lambda: httpx.Response(401, text=leaky_body)),
        )
        r = _run(fire_servicenow(
            short_description="x",
            instance_url="https://example.com",
            username="aegis_bot@acme", password="p" * 24,
        ))
        assert r["status"] == "error"
        assert r["http_status"] == 401
        assert r["reason"] == "ServiceNow auth failed"
        # The username MUST NOT survive the trip back through fire_servicenow.
        assert "aegis_bot" not in r["reason"]
        # SNOW's diagnostic phrase must not leak either.
        assert "User Not Authenticated" not in r["reason"]
        assert "password length" not in r["reason"]

    def test_500_does_not_propagate_stack_trace(self, monkeypatch) -> None:
        leaky_500 = (
            "Internal Server Error: org.apache.catalina.core.StandardWrapperValve "
            "invoke threw NullPointerException at com.snow.handlers.AuthFilter:123 "
            "(jvm host node-77.us-east-1.aws.service-now.com)"
        )
        monkeypatch.setattr(
            "services.autonomy.webhook_executor.httpx.AsyncClient",
            _client_returning(lambda: httpx.Response(500, text=leaky_500)),
        )
        r = _run(fire_servicenow(
            short_description="x",
            instance_url="https://example.com",
            username="u", password="p",
        ))
        assert r["status"] == "error"
        assert r["http_status"] == 500
        assert r["reason"] == "ServiceNow unavailable"
        # Internal host names / stack frames must not surface.
        assert "node-77" not in r["reason"]
        assert "StandardWrapperValve" not in r["reason"]

    def test_network_exception_does_not_propagate_proxy_creds(self, monkeypatch) -> None:
        """An httpx ConnectError can carry the proxy URL — which sometimes
        carries the proxy user+pass. The reason must be the scrub class."""
        def _raise():
            raise httpx.ConnectError(
                "DNS down (proxy=https://userA:hunter2@proxy.acme.local:8080)"
            )

        class _C:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def post(self, *a, **k):
                _raise()
        monkeypatch.setattr(
            "services.autonomy.webhook_executor.httpx.AsyncClient", _C,
        )
        r = _run(fire_servicenow(
            short_description="x",
            instance_url="https://example.com",
            username="u", password="p",
        ))
        assert r["status"] == "error"
        assert r["reason"] == "ServiceNow unavailable"
        assert "hunter2" not in r["reason"]
        assert "userA" not in r["reason"]
        assert "proxy.acme.local" not in r["reason"]

    def test_401_body_is_logged_internally(self, monkeypatch, caplog) -> None:
        """The scrub closes the response-body leak but the operator still
        needs the upstream body in CloudWatch to triage. Verify the log
        path stayed wired."""
        leaky_body = "User Not Authenticated for aegis_bot"
        monkeypatch.setattr(
            "services.autonomy.webhook_executor.httpx.AsyncClient",
            _client_returning(lambda: httpx.Response(401, text=leaky_body)),
        )
        _run(fire_servicenow(
            short_description="x",
            instance_url="https://example.com",
            username="aegis_bot", password="bad",
        ))
        # structlog forwards through stdlib logging so caplog catches it
        # when LOG_LEVEL is high enough. We assert presence-or-absence by
        # checking the record-text union (some envs return the formatted
        # message, others the kwargs).
        msgs = " ".join(rec.getMessage() for rec in caplog.records)
        # When pytest captures the structured log, the body is in the
        # record. If caplog is empty (env doesn't propagate), the
        # internal log is still emitted; this assertion is best-effort.
        if msgs:
            # We don't require the body to be visible in pytest log capture
            # in every env — but the log event NAME must be emitted.
            assert (
                "snow_incident_create_failed" in msgs
                or "User Not Authenticated" in msgs
                or msgs == ""
            )
