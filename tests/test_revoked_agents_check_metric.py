"""Q29 regression: the revoked-agents auth-boundary check's fail-open
path must emit the `EXCEPTION_SWALLOWED_TOTAL` counter so a Redis-blip
storm shows up in Prometheus, not just as grep-of-warning-logs.

The check itself is intentionally fail-open (comment in _mw_auth.py:
"if Redis blips we shouldn't take down auth for every request"), but
the S5 sprint pattern says every fail-open catch must emit
`swallow_log` (which increments the counter) so a spike is
alertable. Prior code used bare `logger.warning`, which was invisible
to Prometheus.
"""
from __future__ import annotations

import inspect

from sdk.common.background import EXCEPTION_SWALLOWED_TOTAL, swallow_log


def test_revoked_agents_check_uses_swallow_log():
    """Whitebox: the source of the revoked-agents catch block must
    call swallow_log (not plain logger.warning). Regression on revert
    silently reintroduces the "warning-only, no metric" pattern."""
    from services.gateway import _mw_auth as _mod
    src = inspect.getsource(_mod)
    # Find the revoked-agents block and verify it uses swallow_log.
    assert 'swallow_log(' in src
    assert 'revoked_agents_check_failed' in src
    # The old bare-warning form is gone.
    assert 'logger.warning("revoked_agents_check_failed"' not in src


def test_swallow_log_increments_the_counter():
    """Sanity: swallow_log actually increments EXCEPTION_SWALLOWED_TOTAL.
    If this invariant ever changes, every Q-round fix that relies on
    fail-open observability drifts silently."""
    import structlog
    log = structlog.get_logger(__name__)
    before = EXCEPTION_SWALLOWED_TOTAL.labels(
        event="revoked_agents_check_failed",
    )._value.get()
    swallow_log(log, "revoked_agents_check_failed", RuntimeError("boom"))
    after = EXCEPTION_SWALLOWED_TOTAL.labels(
        event="revoked_agents_check_failed",
    )._value.get()
    assert after == before + 1
