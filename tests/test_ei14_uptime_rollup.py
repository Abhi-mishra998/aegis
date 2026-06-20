"""Sprint EI-14 — unit tests for scripts/ops/uptime_rollup.py.

Covers the classify() + aggregate() logic against the per-day verify.json
shapes the nightly_verify workflow actually produces.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

# scripts/ops/ is not a package — extend sys.path.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "ops"))

import uptime_rollup as ur  # noqa: E402


# ── classify() ──────────────────────────────────────────────────────────
class TestClassify:
    def test_all_pass_is_green(self):
        assert ur.classify({
            "aevf_v1_v6": "pass", "isolation": "pass",
            "public_probe": "pass", "sbom_cve": "pass", "chaos": "pass",
        }) == "green"

    def test_any_fail_is_incident(self):
        assert ur.classify({
            "aevf_v1_v6": "pass", "isolation": "fail",
            "public_probe": "pass", "sbom_cve": "pass", "chaos": "pass",
        }) == "incident"

    def test_sbom_new_cves_is_incident(self):
        assert ur.classify({
            "aevf_v1_v6": "pass", "isolation": "pass",
            "public_probe": "pass", "sbom_cve": "new-cves", "chaos": "pass",
        }) == "incident"

    def test_skip_status_trips_to_incident(self):
        """A check that ran but returned 'skip' is NOT counted as green
        — better to look amber than claim 100% green when data missing."""
        assert ur.classify({
            "aevf_v1_v6": "skip", "isolation": "pass",
            "public_probe": "pass", "sbom_cve": "pass", "chaos": "pass",
        }) == "incident"

    def test_empty_dict_is_no_data(self):
        assert ur.classify({}) == "no_data"

    def test_none_is_no_data(self):
        assert ur.classify(None) == "no_data"

    def test_partial_data_with_all_pass_seen_is_green(self):
        """Fields that are absent (None) are skipped; the day is green
        if all PRESENT fields are pass and at least one was seen."""
        assert ur.classify({"aevf_v1_v6": "pass"}) == "green"

    def test_case_insensitive_pass(self):
        assert ur.classify({"aevf_v1_v6": "PASS"}) == "green"

    def test_alternate_ok_values(self):
        # The verifier checks success / verified too — both accepted
        assert ur.classify({"aevf_v1_v6": "verified"}) == "green"
        assert ur.classify({"aevf_v1_v6": "success"}) == "green"


# ── aggregate() ─────────────────────────────────────────────────────────
class TestAggregate:
    def _write(self, td: Path, day: str, payload: dict) -> None:
        (td / f"{day}.json").write_text(json.dumps(payload))

    def _green(self) -> dict:
        return {k: "pass" for k in ur.CHECK_FIELDS}

    def _incident(self) -> dict:
        d = self._green()
        d["isolation"] = "fail"
        return d

    def test_empty_dir_is_all_no_data(self, tmp_path):
        r = ur.aggregate(tmp_path, window_days=3, today=dt.date(2026, 6, 20))
        assert r["green_days"] == 0
        assert r["incident_days"] == 0
        assert r["no_data_days"] == 3
        assert r["measured_days"] == 0
        assert r["green_pct"] == 0.0   # no-data days → 0%, not divide-by-zero

    def test_all_green_is_100pct(self, tmp_path):
        for d in ("2026-06-18", "2026-06-19", "2026-06-20"):
            self._write(tmp_path, d, self._green())
        r = ur.aggregate(tmp_path, window_days=3, today=dt.date(2026, 6, 20))
        assert r["green_days"] == 3
        assert r["green_pct"] == 100.0

    def test_one_incident_drops_pct(self, tmp_path):
        self._write(tmp_path, "2026-06-18", self._green())
        self._write(tmp_path, "2026-06-19", self._incident())
        self._write(tmp_path, "2026-06-20", self._green())
        r = ur.aggregate(tmp_path, window_days=3, today=dt.date(2026, 6, 20))
        assert r["incident_days"] == 1
        assert r["green_pct"] == 66.67   # 2/3 rounded to 2 places

    def test_no_data_days_excluded_from_pct(self, tmp_path):
        self._write(tmp_path, "2026-06-19", self._green())
        self._write(tmp_path, "2026-06-20", self._green())
        # 2026-06-18 missing on purpose
        r = ur.aggregate(tmp_path, window_days=3, today=dt.date(2026, 6, 20))
        assert r["green_days"] == 2
        assert r["no_data_days"] == 1
        assert r["measured_days"] == 2
        assert r["green_pct"] == 100.0   # 2 of 2 measured, not 2 of 3

    def test_window_size_respected(self, tmp_path):
        for d in ("2026-06-18", "2026-06-19", "2026-06-20"):
            self._write(tmp_path, d, self._green())
        r = ur.aggregate(tmp_path, window_days=2, today=dt.date(2026, 6, 20))
        assert r["total_days"] == 2
        assert r["window_start_utc"] == "2026-06-19"
        assert r["window_end_utc"]   == "2026-06-20"

    def test_days_array_has_one_entry_per_window_day(self, tmp_path):
        r = ur.aggregate(tmp_path, window_days=7, today=dt.date(2026, 6, 20))
        assert len(r["days"]) == 7
        # First should be 6 days ago.
        assert r["days"][0]["date"] == "2026-06-14"
        assert r["days"][-1]["date"] == "2026-06-20"

    def test_bad_json_treated_as_no_data(self, tmp_path):
        (tmp_path / "2026-06-20.json").write_text("not valid json{")
        r = ur.aggregate(tmp_path, window_days=1, today=dt.date(2026, 6, 20))
        assert r["days"][0]["state"] == "no_data"


# ── main() exit codes ──────────────────────────────────────────────────
class TestMain:
    def test_exit_zero_on_happy_path(self, tmp_path):
        (tmp_path / "2026-06-20.json").write_text(
            json.dumps({k: "pass" for k in ur.CHECK_FIELDS}))
        out = tmp_path / "rollup.json"
        rc = ur.main([
            "--input-dir", str(tmp_path),
            "--out", str(out),
            "--window-days", "1",
        ])
        assert rc == 0
        assert out.exists()
        assert json.loads(out.read_text())["window_days"] == 1

    def test_exit_two_on_missing_input_dir(self, tmp_path):
        rc = ur.main([
            "--input-dir", str(tmp_path / "does-not-exist"),
            "--out", str(tmp_path / "rollup.json"),
        ])
        assert rc == 2
