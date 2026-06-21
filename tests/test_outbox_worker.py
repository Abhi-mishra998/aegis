"""
N5 regression — Audit outbox must treat HTTP 409 from usage-svc as success.

Before the fix, `_forward_to_usage` classified any non-2xx status as either
transient (5xx, 408, 429) or terminal (everything else, including 409). The
billing routes in services/usage/billing_routes/router.py raise HTTPException
with status_code=409 on `ValueError` paths to signal "your event was already
recorded; nothing to do." Treating that as terminal caused the row to be
poisoned after MAX_RETRIES iterations even though the event had already been
delivered successfully on the sync path — silent data-loss-equivalent.

The fix folds 409 into the success branch so the row is marked completed.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Ensure the repo root resolves `services.audit.*` and `sdk.*`. pytest's
# rootdir-detection happens to add `tests/` to sys.path because
# `tests/services/__init__.py` exists (which would otherwise shadow the
# top-level namespace package `services`). Drop that stale entry and put
# the repo root first.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR in sys.path:
    sys.path.remove(_TESTS_DIR)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
# Evict any stale shadow of `services` (e.g. tests/services) the pytest
# collector might have cached before this module ran.
for _mod in [m for m in list(sys.modules) if m == "services" or m.startswith("services.")]:
    _svc = sys.modules.get(_mod)
    _file = getattr(_svc, "__file__", "") or ""
    if _file and "/tests/" in _file:
        del sys.modules[_mod]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _fake_event() -> SimpleNamespace:
    """Minimal stand-in for PendingUsageEvent — only the attributes the
    forwarder reads."""
    return SimpleNamespace(
        tenant_id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool="db.query",
        units=1,
        cost=0.001,
        audit_id=uuid.uuid4(),
    )


def _stub_client(status_code: int, body: str = "") -> MagicMock:
    """An httpx.AsyncClient whose .post() returns a Response with the given
    status code."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = body

    client = MagicMock(spec=httpx.AsyncClient)
    client.post = AsyncMock(return_value=resp)
    return client


# --------------------------------------------------------------------------- #
# _forward_to_usage status-code classification                                #
# --------------------------------------------------------------------------- #


class TestForwardToUsageStatusCodes:
    """Each test exercises one branch of the (success, error, transient) tuple."""

    async def test_200_is_success(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(200)
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is True
        assert err is None
        assert transient is False

    async def test_201_is_success(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(201)
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is True
        assert err is None
        assert transient is False

    async def test_204_is_success(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(204)
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is True
        assert err is None
        assert transient is False

    # --- THE BUG WE'RE FIXING ------------------------------------------------

    async def test_409_is_success_not_terminal(self):
        """N5 regression. 409 = 'already recorded' on the idempotent billing
        path. It must NOT be classified as terminal (which would poison the
        row after MAX_RETRIES) nor as transient (which would retry forever).
        The event has been delivered; mark it completed."""
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(409, body='{"detail":"audit_id already recorded"}')
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is True, (
            "409 must be treated as success — the event was already delivered. "
            "Before the N5 fix this was returned as (False, ..., False) which "
            "incremented retry_count and eventually poisoned the row."
        )
        assert err is None
        assert transient is False

    # --- Transient branch ----------------------------------------------------

    async def test_500_is_transient(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(500, body="internal error")
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert err is not None and err.startswith("http_500:")
        assert transient is True

    async def test_503_is_transient(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(503, body="unavailable")
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is True

    async def test_408_is_transient(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(408)
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is True

    async def test_429_is_transient(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(429)
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is True

    # --- Terminal branch -----------------------------------------------------

    async def test_400_is_terminal(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(400, body="bad payload")
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is False  # terminal: will increment retry_count

    async def test_401_is_terminal(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(401, body="auth")
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is False

    async def test_422_is_terminal(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = _stub_client(422, body="validation")
        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is False

    # --- Network-layer failures ---------------------------------------------

    async def test_connect_error_is_transient(self):
        from services.audit.outbox_worker import _forward_to_usage

        client = MagicMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        ok, err, transient = await _forward_to_usage(client, _fake_event())

        assert ok is False
        assert transient is True
        assert err is not None and err.startswith("network:")


# --------------------------------------------------------------------------- #
# _process_batch — 409 path must take the success branch                       #
# --------------------------------------------------------------------------- #


class TestProcessBatch409:
    """Integration-ish test: feed a 409 through the full batch handler and
    assert the event is marked completed (not retried, not poisoned)."""

    async def test_409_marks_event_completed_not_retried(self, monkeypatch):
        from services.audit import outbox_worker

        event = _fake_event()
        event.retry_count = 0
        event.id = uuid.uuid4()

        # Stub the DB session — _process_batch only needs .execute() (called
        # for both claim + update) and .commit().
        captured_updates: list = []

        class _StubResult:
            def scalars(self):
                return self

            def all(self):
                return [event]

        async def _execute(stmt, *_a, **_k):
            # Distinguish SELECT (returns events) from UPDATE (records params).
            stmt_str = str(stmt).lower()
            if stmt_str.startswith("update"):
                # SQLAlchemy update() compiles via .values(...); grab them.
                captured_updates.append(
                    dict(stmt.compile().params) if hasattr(stmt, "compile") else {}
                )
                return MagicMock()
            return _StubResult()

        db = MagicMock()
        db.execute = AsyncMock(side_effect=_execute)
        db.commit = AsyncMock()

        # Patch _claim_batch directly to bypass real SQL compilation.
        async def _fake_claim(_db):
            return [event]

        monkeypatch.setattr(outbox_worker, "_claim_batch", _fake_claim)

        client = _stub_client(409, body='{"detail":"already recorded"}')

        handled = await outbox_worker._process_batch(db, client)

        assert handled == 1
        assert db.commit.await_count == 1, "batch must commit on success"

        # The UPDATE for a completed event sets status='completed' and clears
        # error_message. The UPDATE for a poisoned/retried event would carry
        # retry_count or status='failed'/'pending'. Inspect the captured params.
        assert captured_updates, "expected at least one UPDATE on success path"
        params = captured_updates[0]
        # SQLAlchemy uses bind keys like 'status' for .values(status=...).
        assert params.get("status") == "completed", (
            f"409 must mark event completed, got params={params}"
        )
        # The terminal branch would set retry_count; the success branch never
        # touches it.
        assert "retry_count" not in params, (
            f"409 must NOT increment retry_count, got params={params}"
        )


# --------------------------------------------------------------------------- #
# Source-level guard: the fix must remain in place                            #
# --------------------------------------------------------------------------- #


def test_source_contains_409_success_branch():
    """Belt-and-braces check: a careless refactor that removes the 409
    success clause should fail this test even if the behavioural tests are
    skipped for any reason."""
    from pathlib import Path

    src = Path("services/audit/outbox_worker.py").read_text()
    assert "resp.status_code == 409" in src, (
        "N5 fix missing: outbox_worker.py must classify HTTP 409 as success"
    )
