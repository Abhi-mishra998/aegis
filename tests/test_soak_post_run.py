"""Unit tests for the soak/fairness post-run validation module.

The soak orchestrator is unattended-CI critical: every minute it
mis-classifies a failed run as PASSED is a minute of regression in
production. These tests pin down every branch of the four validators
so a future refactor can't accidentally widen the pass criteria.

Covered:

* `check_chain_verify` — happy, non-200, violations>0, is_integrous=false.
* `check_reconciliation` — happy (status=VERIFIED + rc=0), gap (rc!=0),
   subprocess parse error.
* `check_flight_timelines_closed` — clean (no in_progress in window),
   leaked rows, empty page (no rows), HTTP error, settle_seconds=0 fast path.
* `check_transparency_roots` — no roots in window (FAIL), happy
  (verify-root true for every root), one root with verify-root false.
* `compute_degradation` (fairness) — clean (no quiet tenant degraded),
   one tenant over budget, missing sample, baseline-zero edge case.
* `parse_per_tenant_p99` — only `/execute/valid|tenant=` rows are picked,
  others ignored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# --------------------------------------------------------------------------- #
# Shared fixtures                                                             #
# --------------------------------------------------------------------------- #


@pytest.fixture
def now_utc() -> datetime:
    return datetime.now(UTC)


def _stub_response(*, status_code: int = 200, body: dict | None = None,
                   request_url: str = "http://gw/x") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    resp.text = json.dumps(body or {})
    return resp


# --------------------------------------------------------------------------- #
# check_chain_verify                                                          #
# --------------------------------------------------------------------------- #


class TestChainVerify:
    def test_happy_path(self):
        from tests.load.post_run_checks import check_chain_verify
        body = {"data": {"is_integrous": True, "violations": 0, "processed_count": 1234}}
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = _stub_response(body=body)
            r = check_chain_verify(gateway_url="http://gw")
        assert r.passed is True
        assert r.detail["violations"] == 0
        assert r.detail["is_integrous"] is True

    def test_violations_present_fails(self):
        from tests.load.post_run_checks import check_chain_verify
        body = {"data": {"is_integrous": True, "violations": 3, "processed_count": 50}}
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = _stub_response(body=body)
            r = check_chain_verify(gateway_url="http://gw")
        assert r.passed is False
        assert r.detail["violations"] == 3

    def test_is_integrous_false_fails(self):
        from tests.load.post_run_checks import check_chain_verify
        body = {"data": {"is_integrous": False, "violations": 0}}
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = _stub_response(body=body)
            r = check_chain_verify(gateway_url="http://gw")
        assert r.passed is False

    def test_non_200_response(self):
        from tests.load.post_run_checks import check_chain_verify
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = _stub_response(status_code=503, body={"detail": "down"})
            r = check_chain_verify(gateway_url="http://gw")
        assert r.passed is False
        assert r.detail["status_code"] == 503

    def test_http_exception(self):
        from tests.load.post_run_checks import check_chain_verify
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.side_effect = RuntimeError("connection refused")
            r = check_chain_verify(gateway_url="http://gw")
        assert r.passed is False
        assert "http_error" in r.detail["error"]


# --------------------------------------------------------------------------- #
# check_reconciliation                                                        #
# --------------------------------------------------------------------------- #


class TestReconciliation:
    def _stub_subprocess(self, *, exit_code: int, stdout: str, stderr: str = ""):
        result = MagicMock()
        result.returncode = exit_code
        result.stdout = stdout
        result.stderr = stderr
        return result

    def test_happy_verified(self):
        from tests.load.post_run_checks import check_reconciliation
        report = {
            "status": "VERIFIED",
            "audit_without_usage_count": 0,
            "usage_without_audit_count": 0,
            "billing_dlq_length": 0,
        }
        with patch("tests.load.post_run_checks.subprocess.run",
                   return_value=self._stub_subprocess(exit_code=0, stdout=json.dumps(report))):
            r = check_reconciliation()
        assert r.passed is True
        assert r.detail["status"] == "VERIFIED"
        assert r.detail["exit_code"] == 0

    def test_gap_detected_fails(self):
        from tests.load.post_run_checks import check_reconciliation
        report = {
            "status": "GAP_DETECTED",
            "audit_without_usage_count": 5,
            "usage_without_audit_count": 0,
        }
        with patch("tests.load.post_run_checks.subprocess.run",
                   return_value=self._stub_subprocess(exit_code=1, stdout=json.dumps(report))):
            r = check_reconciliation()
        assert r.passed is False
        assert r.detail["status"] == "GAP_DETECTED"

    def test_parse_error_when_stdout_not_json(self):
        from tests.load.post_run_checks import check_reconciliation
        with patch("tests.load.post_run_checks.subprocess.run",
                   return_value=self._stub_subprocess(
                       exit_code=2, stdout="not json", stderr="boom")):
            r = check_reconciliation()
        assert r.passed is False
        assert r.detail["error"] == "parse_error"

    def test_subprocess_exception(self):
        from tests.load.post_run_checks import check_reconciliation
        with patch("tests.load.post_run_checks.subprocess.run",
                   side_effect=RuntimeError("exec failed")):
            r = check_reconciliation()
        assert r.passed is False
        assert "exec_error" in r.detail["error"]


# --------------------------------------------------------------------------- #
# check_flight_timelines_closed                                               #
# --------------------------------------------------------------------------- #


class TestFlightTimelinesClosed:
    def _stub_page(self, rows: list[dict]) -> MagicMock:
        return _stub_response(body={"data": rows})

    def _patch_sleep(self, monkeypatch):
        # settle_seconds is real time.sleep — patch it out so tests don't wait.
        monkeypatch.setattr("tests.load.post_run_checks.time.sleep", lambda _: None)

    def test_empty_window_passes(self, monkeypatch, now_utc):
        from tests.load.post_run_checks import check_flight_timelines_closed
        self._patch_sleep(monkeypatch)
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = self._stub_page([])
            r = check_flight_timelines_closed(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(minutes=5),
                run_ended_at=now_utc,
                settle_seconds=0,
            )
        assert r.passed is True
        assert r.detail["leaked_count"] == 0

    def test_all_closed_passes(self, monkeypatch, now_utc):
        from tests.load.post_run_checks import check_flight_timelines_closed
        self._patch_sleep(monkeypatch)
        rows = [
            {"id": "r1", "started_at": (now_utc - timedelta(minutes=4)).isoformat(),
             "status": "ok", "tool": "read_file"},
            {"id": "r2", "started_at": (now_utc - timedelta(minutes=2)).isoformat(),
             "status": "failed", "tool": "exec"},
            # Out-of-window — should be ignored by the cursor walk.
            {"id": "r3", "started_at": (now_utc - timedelta(hours=2)).isoformat(),
             "status": "in_progress", "tool": "x"},
        ]
        with patch("tests.load.post_run_checks.httpx") as h:
            # First page → rows; subsequent pages → empty so the cursor
            # walk terminates the way it does in production.
            h.get.side_effect = [self._stub_page(rows), self._stub_page([])]
            r = check_flight_timelines_closed(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(minutes=5),
                run_ended_at=now_utc,
                settle_seconds=0,
            )
        assert r.passed is True
        assert r.detail["leaked_count"] == 0

    def test_leaked_timelines_fail(self, monkeypatch, now_utc):
        from tests.load.post_run_checks import check_flight_timelines_closed
        self._patch_sleep(monkeypatch)
        rows = [
            {"id": "leak-1", "started_at": (now_utc - timedelta(minutes=3)).isoformat(),
             "status": "in_progress", "tool": "read_file", "request_id": "req-leak-1"},
            {"id": "ok-1", "started_at": (now_utc - timedelta(minutes=2)).isoformat(),
             "status": "ok", "tool": "read_file"},
        ]
        with patch("tests.load.post_run_checks.httpx") as h:
            # First page → rows; subsequent pages → empty so the cursor
            # walk terminates the way it does in production.
            h.get.side_effect = [self._stub_page(rows), self._stub_page([])]
            r = check_flight_timelines_closed(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(minutes=5),
                run_ended_at=now_utc,
                settle_seconds=0,
            )
        assert r.passed is False
        assert r.detail["leaked_count"] == 1
        assert r.detail["leaked_sample"][0]["id"] == "leak-1"

    def test_http_error_fails_safe(self, monkeypatch, now_utc):
        from tests.load.post_run_checks import check_flight_timelines_closed
        self._patch_sleep(monkeypatch)
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.side_effect = RuntimeError("conn refused")
            r = check_flight_timelines_closed(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(minutes=5),
                run_ended_at=now_utc,
                settle_seconds=0,
            )
        assert r.passed is False
        assert "http_error" in r.detail["error"]


# --------------------------------------------------------------------------- #
# check_transparency_roots                                                    #
# --------------------------------------------------------------------------- #


class TestTransparencyRoots:
    def test_no_roots_in_window_fails(self, now_utc):
        from tests.load.post_run_checks import check_transparency_roots
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = _stub_response(body={"data": []})
            r = check_transparency_roots(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(hours=1),
                run_ended_at=now_utc,
            )
        assert r.passed is False
        assert r.detail["error"] == "no_roots_in_window"

    def test_all_verify_true_passes(self, now_utc):
        from tests.load.post_run_checks import check_transparency_roots
        list_resp = _stub_response(body={"data": [
            {"root_date": now_utc.date().isoformat(),
             "signed": {"receipt": {"root_hash": "a" * 64,
                                    "kind": "transparency_root",
                                    "tenant_id": "t",
                                    "root_date": now_utc.date().isoformat()},
                        "signature": "sig", "algorithm": "ed25519",
                        "public_key_fingerprint": "fp"}},
        ]})
        verify_resp = _stub_response(body={"data": {
            "valid": True, "algorithm": "ed25519",
            "expected_fingerprint": "fp", "errors": [],
        }})
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = list_resp
            h.post.return_value = verify_resp
            r = check_transparency_roots(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(hours=1),
                run_ended_at=now_utc,
            )
        assert r.passed is True
        assert r.detail["roots_in_window"] == 1
        assert r.detail["verifications"][0]["valid"] is True

    def test_one_verify_false_fails(self, now_utc):
        from tests.load.post_run_checks import check_transparency_roots
        list_resp = _stub_response(body={"data": [
            {"root_date": now_utc.date().isoformat(), "signed": {
                "receipt": {"root_hash": "a" * 64, "kind": "transparency_root",
                            "tenant_id": "t", "root_date": now_utc.date().isoformat()},
                "signature": "x", "algorithm": "ed25519",
                "public_key_fingerprint": "fp"}},
        ]})
        verify_resp = _stub_response(body={"data": {
            "valid": False, "algorithm": "ed25519",
            "expected_fingerprint": "fp", "errors": ["signature_mismatch"],
        }})
        with patch("tests.load.post_run_checks.httpx") as h:
            h.get.return_value = list_resp
            h.post.return_value = verify_resp
            r = check_transparency_roots(
                gateway_url="http://gw",
                run_started_at=now_utc - timedelta(hours=1),
                run_ended_at=now_utc,
            )
        assert r.passed is False
        assert r.detail["verifications"][0]["valid"] is False
        assert "signature_mismatch" in r.detail["verifications"][0]["errors"]


# --------------------------------------------------------------------------- #
# Fairness: compute_degradation                                               #
# --------------------------------------------------------------------------- #


class TestComputeDegradation:
    def test_clean_passes(self):
        from tests.load.fairness import compute_degradation
        passed, rep = compute_degradation(
            baseline={"a": 100, "b": 110, "c": 90, "d": 105},
            burst={"a": 105, "b": 115, "c": 95, "d": 108},
            quiet_labels=["a", "b", "c", "d"],
            max_degradation_pct=20.0,
        )
        assert passed is True
        assert rep["worst_degradation_pct"] <= 20.0

    def test_one_tenant_over_budget_fails(self):
        from tests.load.fairness import compute_degradation
        passed, rep = compute_degradation(
            baseline={"a": 100, "b": 110},
            burst={"a": 105, "b": 200},  # b degraded 81% -> > 20%
            quiet_labels=["a", "b"],
            max_degradation_pct=20.0,
        )
        assert passed is False
        entry_b = next(e for e in rep["per_tenant"] if e["tenant"] == "b")
        assert entry_b["ok"] is False
        assert entry_b["delta_pct"] > 20.0
        assert rep["worst_degradation_pct"] > 20.0

    def test_missing_sample_fails_safe(self):
        from tests.load.fairness import compute_degradation
        passed, rep = compute_degradation(
            baseline={"a": 100},
            burst={},  # quiet tenant ran during burst but no sample shown
            quiet_labels=["a"],
            max_degradation_pct=20.0,
        )
        assert passed is False
        assert rep["per_tenant"][0].get("error") == "missing_sample"

    def test_baseline_zero_edge_case_does_not_div_zero(self):
        from tests.load.fairness import compute_degradation
        passed, rep = compute_degradation(
            baseline={"a": 0.0},
            burst={"a": 0.0},
            quiet_labels=["a"],
            max_degradation_pct=20.0,
        )
        assert passed is True
        assert rep["worst_degradation_pct"] == 0.0


# --------------------------------------------------------------------------- #
# parse_per_tenant_p99                                                        #
# --------------------------------------------------------------------------- #


def test_parse_per_tenant_p99_ignores_non_valid_rows(tmp_path: Path):
    from tests.load.fairness import parse_per_tenant_p99
    csv = tmp_path / "stats.csv"
    csv.write_text(
        "Type,Name,Request Count,Failure Count,50%,95%,99%\n"
        "POST,/execute/valid|tenant=alpha,1000,0,80,150,210\n"
        "POST,/execute/valid|tenant=beta,1000,0,80,150,330\n"
        "POST,/execute/injection|tenant=alpha,200,0,90,180,250\n"   # ignored
        "POST,Aggregated,2200,0,82,160,300\n"                       # ignored
    )
    out = parse_per_tenant_p99(csv)
    assert out == {"alpha": 210.0, "beta": 330.0}


def test_parse_per_tenant_p99_missing_file():
    from tests.load.fairness import parse_per_tenant_p99
    out = parse_per_tenant_p99(Path("/nonexistent/path.csv"))
    assert out == {}
