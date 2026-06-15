"""
Sprint 3 — Shadow Mode unit tests.

Covers the pure-function pieces:
  - _shadow_mode_active(request) for str + datetime + None.
  - _canonical_role() projection inside identity/router.py.
  - SHADOW_DOWNGRADES_TOTAL counter exists with the right label set.

Full middleware integration (decision mutation + audit row + SSE) is a
live concern — the resolver tests pin the boundary, and the live smoke
probe after deploy confirms end-to-end behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sdk.common.roles import Role
from services.gateway.middleware import SHADOW_DOWNGRADES_TOTAL, _shadow_mode_active


def _req(**state):
    """Synthesise a Request-like object with .state attributes."""
    return SimpleNamespace(state=SimpleNamespace(**state))


# ───────────────────────────────────────────────────────────────────────
# _shadow_mode_active
# ───────────────────────────────────────────────────────────────────────


def test_shadow_inactive_when_state_missing():
    assert _shadow_mode_active(SimpleNamespace(state=SimpleNamespace())) is False


def test_shadow_inactive_when_value_none():
    assert _shadow_mode_active(_req(shadow_mode_until=None)) is False


def test_shadow_active_when_datetime_future():
    future = datetime.utcnow() + timedelta(days=10)
    assert _shadow_mode_active(_req(shadow_mode_until=future)) is True


def test_shadow_inactive_when_datetime_past():
    past = datetime.utcnow() - timedelta(seconds=30)
    assert _shadow_mode_active(_req(shadow_mode_until=past)) is False


def test_shadow_active_when_iso_string_future():
    future_iso = (
        datetime.now(tz=timezone.utc) + timedelta(days=3)
    ).isoformat()
    assert _shadow_mode_active(_req(shadow_mode_until=future_iso)) is True


def test_shadow_inactive_when_iso_string_past():
    past_iso = (
        datetime.now(tz=timezone.utc) - timedelta(hours=1)
    ).isoformat()
    assert _shadow_mode_active(_req(shadow_mode_until=past_iso)) is False


def test_shadow_handles_z_suffix_iso_string():
    """Some serializers emit `...Z` instead of `+00:00`."""
    future_iso = (
        datetime.now(tz=timezone.utc) + timedelta(days=1)
    ).isoformat().replace("+00:00", "Z")
    assert _shadow_mode_active(_req(shadow_mode_until=future_iso)) is True


def test_shadow_falls_through_on_garbage_string():
    assert _shadow_mode_active(_req(shadow_mode_until="not-an-iso-date")) is False


def test_shadow_falls_through_on_unsupported_type():
    assert _shadow_mode_active(_req(shadow_mode_until=12345)) is False


# ───────────────────────────────────────────────────────────────────────
# SHADOW_DOWNGRADES_TOTAL counter
# ───────────────────────────────────────────────────────────────────────


def test_shadow_downgrades_counter_has_expected_labels():
    # The counter is built with labels=["tenant_id", "original_action"];
    # exercising .labels() with the wrong label set raises ValueError.
    SHADOW_DOWNGRADES_TOTAL.labels(
        tenant_id="t1", original_action="deny",
    ).inc()
    assert SHADOW_DOWNGRADES_TOTAL._labelnames == ("tenant_id", "original_action")


# ───────────────────────────────────────────────────────────────────────
# Identity router _canonical_role helper
# ───────────────────────────────────────────────────────────────────────


def test_identity_canonical_role_projects_legacy_values():
    from services.identity.router import _canonical_role
    assert _canonical_role("ADMIN") == "ADMIN"
    assert _canonical_role("OWNER") == "OWNER"
    assert _canonical_role("SECURITY") == "SECURITY_ANALYST"
    assert _canonical_role("AUDITOR") == "READ_ONLY"
    assert _canonical_role("VIEWER") == "READ_ONLY"
    assert _canonical_role(None) == "READ_ONLY"


def test_role_enum_includes_owner_for_exit_shadow_mode():
    """The gateway's verify_role(Role.OWNER) gate uses this exact value."""
    assert Role.OWNER.value == "OWNER"
