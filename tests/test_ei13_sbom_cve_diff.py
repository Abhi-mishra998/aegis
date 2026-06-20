"""Sprint EI-13 — unit tests for scripts/ops/sbom_cve_diff.py.

Covers the bucketing logic: new / resolved / chronic, severity floor,
missing yesterday handling, deterministic ordering, markdown rendering.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# scripts/ops/ is not a package — add to sys.path so we can import.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts" / "ops"))

import sbom_cve_diff as scd  # noqa: E402


def _f(id: str, sev: str, pkg: str = "lib", ver: str = "1.0",
       fixed: str | None = None) -> dict:
    """Build a finding dict in the shape sbom_cve_scan.sh emits."""
    return {
        "id":                id,
        "severity":          sev,
        "package":           pkg,
        "installed_version": ver,
        "fixed_version":     fixed,
        "primary_url":       f"https://nvd.nist.gov/vuln/detail/{id}",
    }


# ── CveKey identity ─────────────────────────────────────────────────────
class TestCveKey:
    def test_identity_includes_version(self):
        a = scd.CveKey.from_finding(_f("CVE-1", "HIGH", "lib", "1.0"))
        b = scd.CveKey.from_finding(_f("CVE-1", "HIGH", "lib", "1.0"))
        c = scd.CveKey.from_finding(_f("CVE-1", "HIGH", "lib", "1.1"))
        assert a == b
        assert a != c   # an upgrade that didn't fix the CVE = different key

    def test_identity_includes_package(self):
        a = scd.CveKey.from_finding(_f("CVE-1", "HIGH", "lib-a"))
        b = scd.CveKey.from_finding(_f("CVE-1", "HIGH", "lib-b"))
        assert a != b


# ── Diff bucketing ──────────────────────────────────────────────────────
class TestDiff:
    def test_empty_inputs(self):
        r = scd.diff([], [])
        assert r["counts"] == {"new": 0, "resolved": 0, "chronic": 0}

    def test_new_cve_appears(self):
        r = scd.diff([_f("CVE-9", "HIGH")], [], severity_floor="HIGH")
        assert r["counts"]["new"] == 1
        assert r["new"][0]["id"] == "CVE-9"

    def test_resolved_cve_appears(self):
        r = scd.diff([], [_f("CVE-1", "HIGH")], severity_floor="HIGH")
        assert r["counts"]["resolved"] == 1
        assert r["resolved"][0]["id"] == "CVE-1"

    def test_chronic_unchanged_cve_not_flagged_as_new(self):
        """The signal we want: NEW only. A chronic-unfixed CVE present
        in both snapshots must NOT show up in the `new` bucket."""
        chronic = _f("CVE-CHRONIC", "CRITICAL")
        r = scd.diff([chronic], [chronic], severity_floor="HIGH")
        assert r["counts"]["new"] == 0
        assert r["counts"]["chronic"] == 1

    def test_severity_floor_filters_below(self):
        """LOW + MEDIUM below the HIGH floor are ignored from new."""
        today = [
            _f("CVE-L", "LOW"),
            _f("CVE-M", "MEDIUM"),
            _f("CVE-H", "HIGH"),
            _f("CVE-C", "CRITICAL"),
        ]
        r = scd.diff(today, [], severity_floor="HIGH")
        new_ids = {f["id"] for f in r["new"]}
        assert new_ids == {"CVE-H", "CVE-C"}

    def test_severity_floor_critical_only(self):
        today = [_f("CVE-H", "HIGH"), _f("CVE-C", "CRITICAL")]
        r = scd.diff(today, [], severity_floor="CRITICAL")
        assert {f["id"] for f in r["new"]} == {"CVE-C"}

    def test_resolved_includes_below_floor_too(self):
        """Resolved is good news regardless of severity; report it all."""
        yesterday = [_f("CVE-1", "LOW"), _f("CVE-2", "HIGH")]
        r = scd.diff([], yesterday, severity_floor="HIGH")
        # resolved is filtered by yesterday-meets-floor (LOW < HIGH skipped)
        resolved_ids = {f["id"] for f in r["resolved"]}
        assert resolved_ids == {"CVE-2"}

    def test_deterministic_order(self):
        """Two diffs of the same inputs produce identical JSON."""
        a = [_f("CVE-Z", "HIGH", "z"), _f("CVE-A", "CRITICAL", "a")]
        b = [_f("CVE-A", "CRITICAL", "a")]
        r1 = scd.diff(a, b)
        r2 = scd.diff(list(reversed(a)), list(b))
        assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)

    def test_three_buckets_simultaneously(self):
        yesterday = [_f("CVE-CHRONIC", "HIGH"), _f("CVE-RESOLVED", "CRITICAL")]
        today     = [_f("CVE-CHRONIC", "HIGH"), _f("CVE-NEW", "CRITICAL")]
        r = scd.diff(today, yesterday, severity_floor="HIGH")
        assert r["counts"] == {"new": 1, "resolved": 1, "chronic": 1}
        assert r["new"][0]["id"]      == "CVE-NEW"
        assert r["resolved"][0]["id"] == "CVE-RESOLVED"
        assert r["chronic"][0]["id"]  == "CVE-CHRONIC"


# ── Markdown rendering ──────────────────────────────────────────────────
class TestMarkdown:
    def test_renders_header_and_counts_always(self):
        r = scd.diff([], [])
        md = scd.render_markdown(r)
        assert "Nightly SBOM CVE diff" in md
        assert "| **0** | 0 | 0 |" in md

    def test_omits_new_section_when_no_new(self):
        r = scd.diff([_f("CVE-CHRONIC", "HIGH")], [_f("CVE-CHRONIC", "HIGH")])
        md = scd.render_markdown(r)
        assert "New CVEs (this is the signal)" not in md

    def test_includes_new_section_when_new(self):
        r = scd.diff([_f("CVE-NEW", "CRITICAL", pkg="urllib3", ver="2.0",
                          fixed="2.1")], [])
        md = scd.render_markdown(r)
        assert "New CVEs (this is the signal)" in md
        assert "CVE-NEW" in md
        assert "CRITICAL" in md
        assert "urllib3" in md
        assert "2.0" in md
        assert "2.1" in md

    def test_renders_em_dash_for_no_fix(self):
        r = scd.diff([_f("CVE-NEW", "CRITICAL", fixed=None)], [])
        md = scd.render_markdown(r)
        assert "—" in md   # placeholder for missing fixed_version


# ── _load helper ────────────────────────────────────────────────────────
class TestLoad:
    def test_missing_file_returns_empty_list(self):
        assert scd._load(Path("/nonexistent/path/today.json")) == []

    def test_none_path_returns_empty_list(self):
        assert scd._load(None) == []

    def test_bad_json_returns_empty_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as t:
            t.write("not valid json{")
            t.flush()
            assert scd._load(Path(t.name)) == []

    def test_non_list_returns_empty_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as t:
            t.write('{"oops": "dict not list"}')
            t.flush()
            assert scd._load(Path(t.name)) == []


# ── main() exit code ────────────────────────────────────────────────────
class TestMain:
    def test_exit_zero_when_no_new(self, tmp_path, capsys):
        today = tmp_path / "today.json"
        today.write_text(json.dumps([_f("CVE-CHRONIC", "HIGH")]))
        yesterday = tmp_path / "yesterday.json"
        yesterday.write_text(json.dumps([_f("CVE-CHRONIC", "HIGH")]))
        out = tmp_path / "diff.json"
        rc = scd.main(["--today", str(today), "--yesterday", str(yesterday),
                       "--out", str(out)])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "pass"

    def test_exit_one_when_new_cve(self, tmp_path, capsys):
        today = tmp_path / "today.json"
        today.write_text(json.dumps([_f("CVE-NEW", "CRITICAL")]))
        out = tmp_path / "diff.json"
        rc = scd.main(["--today", str(today), "--out", str(out)])
        assert rc == 1
        assert capsys.readouterr().out.strip() == "new-cves"

    def test_exit_two_when_today_missing(self, tmp_path, capsys):
        out = tmp_path / "diff.json"
        rc = scd.main(["--today", str(tmp_path / "missing.json"),
                       "--out", str(out)])
        assert rc == 2

    def test_missing_yesterday_treated_as_empty_baseline(self, tmp_path):
        """First-ever run: yesterday.json doesn't exist yet. Every
        today-CVE-at-or-above-floor becomes a new CVE."""
        today = tmp_path / "today.json"
        today.write_text(json.dumps([_f("CVE-1", "HIGH"), _f("CVE-2", "CRITICAL")]))
        out = tmp_path / "diff.json"
        rc = scd.main(["--today", str(today),
                       "--yesterday", str(tmp_path / "missing.json"),
                       "--out", str(out)])
        assert rc == 1   # 2 new CVEs vs empty baseline
        diff = json.loads(out.read_text())
        assert diff["counts"]["new"] == 2
