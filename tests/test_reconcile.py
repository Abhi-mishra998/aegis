"""Unit tests for the billing reconciliation sprint (2026-05-15).

Covers:

* ReconciliationReport.finalize — status transitions (VERIFIED |
  GAP_DETECTED | ERROR), `is_integrous` truth table, ts is set.
* run_reconciliation:
  - happy path with identical sets → VERIFIED, exit code 0
  - audit-side gap → GAP_DETECTED, sample contains the offending IDs
  - usage-side gap (forced) → GAP_DETECTED, sample contains the offending IDs
  - dual-direction gap → both samples populated, single status
  - DB error → ERROR status + error string + non-zero exit code
* CLI exit code reflects integrity.
* Report shape contains every field the docs/Alertmanager rules expect.

The tests patch the three data-access helpers so we don't need a live
Postgres/Redis instance.
"""

from __future__ import annotations

import sys
from dataclasses import asdict

# psycopg2 may not be installed locally — the reconcile script imports it at
# module load and SystemExits on ImportError. Stub it conservatively. We do
# NOT stub `redis` here because the real `redis` package is required for
# `redis.asyncio` (used by other test modules in the same pytest run);
# overwriting it would break test isolation.

class _FakePsycopg2:
    class extras:
        pass

    @staticmethod
    def connect(*_a, **_k):  # pragma: no cover — patched per-test
        raise AssertionError("psycopg2.connect should be patched")


sys.modules.setdefault("psycopg2", _FakePsycopg2())
sys.modules.setdefault("psycopg2.extras", _FakePsycopg2.extras())

from scripts.ops import reconcile  # noqa: E402

# --------------------------------------------------------------------------- #
# ReconciliationReport.finalize                                               #
# --------------------------------------------------------------------------- #


class TestReportFinalize:
    def test_clean_state_is_verified(self):
        r = reconcile.ReconciliationReport(tenant_id="t").finalize()
        assert r.is_integrous is True
        assert r.status == "VERIFIED"
        assert r.ts > 0

    def test_audit_side_gap_is_gap_detected(self):
        r = reconcile.ReconciliationReport(tenant_id="t", audit_without_usage_count=1).finalize()
        assert r.is_integrous is False
        assert r.status == "GAP_DETECTED"

    def test_usage_side_gap_is_gap_detected(self):
        r = reconcile.ReconciliationReport(tenant_id="t", usage_without_audit_count=5).finalize()
        assert r.is_integrous is False
        assert r.status == "GAP_DETECTED"

    def test_nonzero_billing_dlq_is_gap(self):
        r = reconcile.ReconciliationReport(tenant_id="t", billing_dlq_length=2).finalize()
        assert r.is_integrous is False
        assert r.status == "GAP_DETECTED"

    def test_error_string_overrides_status(self):
        r = reconcile.ReconciliationReport(tenant_id="t", error="db_error: boom").finalize()
        assert r.status == "ERROR"


# --------------------------------------------------------------------------- #
# run_reconciliation — patches data accessors                                 #
# --------------------------------------------------------------------------- #


def _patch_data(monkeypatch, *, audit_ids: set[str], usage_ids: set[str],
                audit_total: int | None = None, usage_total: int | None = None,
                dlq_billing: int = 0, dlq_audit: int = 0,
                outbox_age: int = 0,
                raise_audit: Exception | None = None,
                raise_usage: Exception | None = None) -> None:
    """Patch the four module-level helpers run_reconciliation depends on."""
    def _aud(*_a, **_k):
        if raise_audit:
            raise raise_audit
        return set(audit_ids), int(audit_total if audit_total is not None else len(audit_ids))

    def _all_audit(*_a, **_k):
        if raise_audit:
            raise raise_audit
        return set(audit_ids)

    def _use(*_a, **_k):
        if raise_usage:
            raise raise_usage
        return set(usage_ids), int(usage_total if usage_total is not None else len(usage_ids))

    def _outbox(*_a, **_k):
        return outbox_age

    def _dlq(*_a, **_k):
        return dlq_billing, dlq_audit

    monkeypatch.setattr(reconcile, "_fetch_audit_ids", _aud)
    monkeypatch.setattr(reconcile, "_fetch_all_audit_request_ids", _all_audit)
    monkeypatch.setattr(reconcile, "_fetch_usage_ids", _use)
    monkeypatch.setattr(reconcile, "_outbox_pending_age_seconds", _outbox)
    monkeypatch.setattr(reconcile, "_redis_dlq_lengths", _dlq)


def _run(monkeypatch, **overrides) -> reconcile.ReconciliationReport:
    _patch_data(monkeypatch, **overrides)
    return reconcile.run_reconciliation(
        audit_db="x", usage_db="y", redis_url="redis://z",
        tenant_id="t", grace_seconds=60,
    )


class TestRunReconciliation:
    def test_identical_sets_pass(self, monkeypatch):
        ids = {f"id-{i}" for i in range(5)}
        r = _run(monkeypatch, audit_ids=ids, usage_ids=ids)
        assert r.is_integrous is True
        assert r.status == "VERIFIED"
        assert r.audit_without_usage_count == 0
        assert r.usage_without_audit_count == 0
        assert r.billable_audit_count == 5
        assert r.usage_record_count == 5

    def test_audit_side_gap_surfaces_samples(self, monkeypatch):
        audit_ids = {"a-1", "a-2", "a-3", "a-4"}
        usage_ids = {"a-1", "a-2"}
        r = _run(monkeypatch, audit_ids=audit_ids, usage_ids=usage_ids)
        assert r.status == "GAP_DETECTED"
        assert r.audit_without_usage_count == 2
        # Deterministic ordering (sorted) — easy to compare in CI.
        assert r.audit_without_usage_sample == ["a-3", "a-4"]
        assert r.usage_without_audit_sample == []

    def test_usage_side_gap_surfaces_samples(self, monkeypatch):
        audit_ids = {"a-1"}
        usage_ids = {"a-1", "ghost-1", "ghost-2"}
        r = _run(monkeypatch, audit_ids=audit_ids, usage_ids=usage_ids)
        assert r.status == "GAP_DETECTED"
        assert r.usage_without_audit_count == 2
        assert r.usage_without_audit_sample == ["ghost-1", "ghost-2"]
        assert r.audit_without_usage_count == 0

    def test_sample_size_is_capped(self, monkeypatch):
        usage_ids = {f"ghost-{i:03d}" for i in range(25)}
        r = _run(monkeypatch, audit_ids=set(), usage_ids=usage_ids)
        # SAMPLE_SIZE is 10 in the module.
        assert len(r.usage_without_audit_sample) == reconcile.SAMPLE_SIZE
        assert r.usage_without_audit_count == 25

    def test_dual_direction_gap_single_status(self, monkeypatch):
        r = _run(monkeypatch, audit_ids={"a"}, usage_ids={"b"})
        assert r.status == "GAP_DETECTED"
        assert r.audit_without_usage_count == 1
        assert r.usage_without_audit_count == 1

    def test_dlq_pressure_alone_is_gap(self, monkeypatch):
        ids = {"a"}
        r = _run(monkeypatch, audit_ids=ids, usage_ids=ids, dlq_billing=3)
        assert r.is_integrous is False
        assert r.status == "GAP_DETECTED"
        assert r.billing_dlq_length == 3

    def test_audit_db_error_is_error_status(self, monkeypatch):
        r = _run(monkeypatch, audit_ids=set(), usage_ids=set(),
                 raise_audit=RuntimeError("connection refused"))
        assert r.status == "ERROR"
        assert "connection refused" in (r.error or "")
        # ERROR is also a non-integrous state — exit code must be non-zero.
        assert r.is_integrous is False

    def test_outbox_age_is_reported(self, monkeypatch):
        ids = {"a"}
        r = _run(monkeypatch, audit_ids=ids, usage_ids=ids, outbox_age=42)
        assert r.outbox_pending_age_seconds == 42
        # Outbox age alone does NOT mark non-integrous (it's a warning signal,
        # not a definitive gap).
        assert r.is_integrous is True

    def test_grace_window_excludes_recent_rows(self, monkeypatch):
        """The grace filter is applied inside _fetch_audit_ids (SQL-level).
        The accessor returns the *post-grace* set as the diff source, while
        also exposing the *total* count for the headline. Verify the report
        uses both correctly."""
        # 5 total billable rows, only 2 past the grace window.
        recent_only_ids = {"old-1", "old-2"}
        r = _run(monkeypatch, audit_ids=recent_only_ids, usage_ids={"old-1", "old-2"},
                 audit_total=5)
        assert r.billable_audit_count == 5  # headline includes recent
        assert r.audit_without_usage_count == 0  # diff respects grace


# --------------------------------------------------------------------------- #
# Report shape — must contain every field the docs / alerts depend on        #
# --------------------------------------------------------------------------- #


def test_report_contains_all_required_fields(monkeypatch):
    r = _run(monkeypatch, audit_ids={"a"}, usage_ids={"a"})
    body = asdict(r)
    required = {
        "tenant_id",
        "billable_audit_count",
        "usage_record_count",
        "audit_without_usage_count",
        "usage_without_audit_count",
        "audit_without_usage_sample",
        "usage_without_audit_sample",
        "billing_dlq_length",
        "audit_dlq_length",
        "outbox_pending_age_seconds",
        "is_integrous",
        "status",
        "ts",
    }
    missing = required - set(body)
    assert not missing, f"report missing required fields: {missing}"


# --------------------------------------------------------------------------- #
# CLI exit-code behaviour                                                     #
# --------------------------------------------------------------------------- #


def test_cli_returns_zero_on_clean_state(monkeypatch, capsys):
    _patch_data(monkeypatch, audit_ids={"a"}, usage_ids={"a"})
    monkeypatch.setattr(sys, "argv", ["reconcile.py", "--json"])
    rc = reconcile.main()
    assert rc == 0
    out = capsys.readouterr().out
    assert '"status": "VERIFIED"' in out


def test_cli_returns_nonzero_on_gap(monkeypatch, capsys):
    _patch_data(monkeypatch, audit_ids={"a"}, usage_ids={"a", "ghost"})
    monkeypatch.setattr(sys, "argv", ["reconcile.py", "--json"])
    rc = reconcile.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert '"status": "GAP_DETECTED"' in out
    assert "ghost" in out  # the orphan ID appears in the sample


def test_cli_returns_nonzero_on_error(monkeypatch, capsys):
    _patch_data(monkeypatch, audit_ids=set(), usage_ids=set(),
                raise_audit=RuntimeError("db gone"))
    monkeypatch.setattr(sys, "argv", ["reconcile.py", "--json"])
    rc = reconcile.main()
    assert rc == 1
    out = capsys.readouterr().out
    assert '"status": "ERROR"' in out
