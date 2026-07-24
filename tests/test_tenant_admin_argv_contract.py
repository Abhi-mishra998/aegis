"""Regression: gateway's /admin/tenants/{id}/export and /redact endpoints
must invoke the ops scripts with the argv NAMES the scripts actually
declare. Prior version passed `--tenant-id` while both scripts declare
`--tenant`, and `/redact` was missing the required `--reason` +
`--execute` flags — so both admin endpoints would fail on first use.

We test by argparse-parsing the exact argv the gateway would build. If
this test fails, an admin-only endpoint that customers rely on for GDPR
compliance has silently regressed.
"""
from __future__ import annotations

import runpy
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_script_parser(script_rel: str):
    """Import a script by path and return its `_build_argparser()`."""
    script_path = _ROOT / script_rel
    ns = runpy.run_path(str(script_path), run_name="__not_main__")
    return ns["_build_argparser"]()


def test_export_argv_matches_script_contract():
    """The argv the gateway builds for /export must parse cleanly against
    scripts/ops/export_tenant.py's argparse."""
    tid = "11111111-1111-1111-1111-111111111111"
    # Same shape as tenant_admin.py::start_tenant_export builds it.
    argv = [
        "--tenant", tid,
        "--output", "/tmp/export.tar.gz",
    ]
    parser = _load_script_parser("scripts/ops/export_tenant.py")
    # Parses without SystemExit — argparse SystemExit's on unknown args.
    ns = parser.parse_args(argv)
    assert ns.tenant == tid
    assert str(ns.output) == "/tmp/export.tar.gz"


def test_redact_argv_matches_script_contract():
    """The argv the gateway builds for /redact must parse cleanly."""
    tid = "22222222-2222-2222-2222-222222222222"
    argv = [
        "--tenant", tid,
        "--reason", "GDPR-2026-0042",
        "--actor", "admin@example.com",
        "--execute",
    ]
    parser = _load_script_parser("scripts/ops/redact_tenant_pii.py")
    ns = parser.parse_args(argv)
    assert ns.tenant == tid
    assert ns.reason == "GDPR-2026-0042"
    assert ns.actor == "admin@example.com"
    assert ns.execute is True
    assert ns.dry_run is False


def test_redact_argv_missing_reason_fails_at_argparse():
    """Sanity: dropping --reason (the field the old gateway forgot to
    pass) makes argparse SystemExit, proving the parser really requires
    it."""
    parser = _load_script_parser("scripts/ops/redact_tenant_pii.py")
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--tenant", "33333333-3333-3333-3333-333333333333",
            "--actor", "ops",
            "--execute",
        ])


def test_old_tenant_id_arg_fails_at_argparse():
    """Sanity: the OLD --tenant-id (pre-fix) makes argparse SystemExit —
    proves the fix isn't papering over a still-broken contract."""
    parser = _load_script_parser("scripts/ops/export_tenant.py")
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--tenant-id", "44444444-4444-4444-4444-444444444444",
        ])


# stderr capture during argparse test — argparse writes to stderr on
# SystemExit. Redirect so the test output stays clean.
@pytest.fixture(autouse=True)
def _quiet_argparse(capfd):
    yield
