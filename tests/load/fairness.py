#!/usr/bin/env python3
"""Multi-tenant fairness test.

Two phases, both unattended:

  Phase A — baseline. 50 users against a single quiet tenant.
            5-minute window. Records per-tenant p99 from locust CSV.
  Phase B — burst. 4 quiet tenants @ 50 users each (200 total) +
            1 noisy tenant @ 500 users (700 total). 5-minute window.
            Records per-tenant p99 again.

Fairness criterion: each quiet tenant's p99 in phase B must not exceed
its phase A p99 by more than `--max-degradation-pct` (default 20%).
If it does, the system's tenant isolation under load is insufficient
and the script exits non-zero with a structured failure report.

Outputs (reports/soak/{timestamp}/):

    baseline_locust_stats.csv  — phase A aggregate
    burst_locust_stats.csv     — phase B aggregate
    fairness_report.json       — per-tenant p99 before/after + degradation

Usage:

    python tests/load/fairness.py             # full 2×5min run
    python tests/load/fairness.py --duration 30s --quiet-users 5 --noisy-users 20
"""

from __future__ import annotations

import argparse
import csv as _csv
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tenant provision helpers are shared with the soak harness — single source
# of truth for tenant lifecycle.
from tests.load.soak import _provision_tenants, _teardown_tenants  # noqa: E402


def _run_phase(
    *,
    phase: str,
    gateway_url: str,
    manifest_path: Path,
    output_prefix: Path,
    users: int,
    spawn_rate: int,
    duration: str,
) -> int:
    locust_bin = shutil.which("locust") or shutil.which("locust.exe")
    if not locust_bin:
        raise SystemExit("locust not found in PATH")
    cmd = [
        locust_bin, "-f", "tests/load/soak_user.py",
        "--headless", "--only-summary",
        "-u", str(users), "-r", str(spawn_rate), "-t", duration,
        "--host", gateway_url,
        "--csv", str(output_prefix),
    ]
    env = os.environ.copy()
    env["SOAK_MANIFEST"] = str(manifest_path)
    print(f"[fairness:{phase}] launching: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env=env, cwd=str(_REPO_ROOT))
    return proc.returncode


def parse_per_tenant_p99(stats_csv: Path) -> dict[str, float]:
    """Extract `/execute/valid|tenant=<label>` p99 for every tenant.

    Returns `{label: p99_ms}` — labels come from the request-name suffix
    the soak user injected.
    """
    out: dict[str, float] = {}
    if not stats_csv.exists():
        return out
    with stats_csv.open() as fh:
        reader = _csv.DictReader(fh)
        for row in reader:
            name = row.get("Name") or ""
            if not name.startswith("/execute/valid|tenant="):
                continue
            label = name.split("tenant=", 1)[1]
            p99 = row.get("99%") or row.get("99%ile")
            try:
                out[label] = float(p99)
            except (TypeError, ValueError):
                continue
    return out


def compute_degradation(
    *,
    baseline: dict[str, float],
    burst: dict[str, float],
    quiet_labels: list[str],
    max_degradation_pct: float,
) -> tuple[bool, dict]:
    """Return `(passed, report)`. `passed=False` if ANY quiet tenant's
    burst p99 exceeds baseline p99 by more than max_degradation_pct."""
    per_tenant: list[dict] = []
    worst_pct = 0.0
    for lbl in quiet_labels:
        b = baseline.get(lbl)
        u = burst.get(lbl)
        if b is None or u is None:
            per_tenant.append({
                "tenant":     lbl,
                "baseline":   b, "burst": u,
                "error":      "missing_sample",
            })
            continue
        pct = (0.0 if u <= 0 else float("inf")) if b <= 0 else (u - b) / b * 100.0
        worst_pct = max(worst_pct, pct)
        per_tenant.append({
            "tenant":   lbl,
            "baseline": round(b, 2),
            "burst":    round(u, 2),
            "delta_pct": round(pct, 2),
            "ok":       pct <= max_degradation_pct,
        })
    passed = all(
        e.get("ok", False) and "error" not in e
        for e in per_tenant
    ) if per_tenant else False
    return passed, {
        "per_tenant": per_tenant,
        "worst_degradation_pct": round(worst_pct, 2),
        "max_degradation_pct":   max_degradation_pct,
        "passed":                passed,
    }


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--quiet-users", type=int, default=50,
                   help="users per quiet tenant in BOTH phases (default 50)")
    p.add_argument("--noisy-users", type=int, default=500,
                   help="users on the noisy tenant in phase B (default 500)")
    p.add_argument("--quiet-tenant-count", type=int, default=4,
                   help="number of quiet tenants (default 4)")
    p.add_argument("--duration", default="5m",
                   help="locust -t per phase (default 5m)")
    p.add_argument("--max-degradation-pct", type=float, default=20.0,
                   help="max allowed quiet-tenant p99 degradation in pct (default 20)")
    p.add_argument("--gateway-url", default=os.environ.get("GATEWAY_URL", "http://localhost:8000"))
    p.add_argument("--internal-secret", default=os.environ.get("INTERNAL_SECRET", ""))
    p.add_argument("--reports-dir", default=os.environ.get("REPORTS_DIR", "reports/soak"))
    p.add_argument("--no-teardown", action="store_true")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    if not args.internal_secret:
        print("ERROR: INTERNAL_SECRET (or --internal-secret) is required",
              file=sys.stderr)
        return 2

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _REPO_ROOT / args.reports_dir / f"{ts}-fairness"
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Provision quiet + noisy tenants, single shared manifest ───────
    quiet_tenants = _provision_tenants(
        gateway_url=args.gateway_url, internal_secret=args.internal_secret,
        n=int(args.quiet_tenant_count), label_prefix="quiet",
    )
    noisy_tenants = _provision_tenants(
        gateway_url=args.gateway_url, internal_secret=args.internal_secret,
        n=1, label_prefix="noisy",
    )
    all_tenants = quiet_tenants + noisy_tenants
    quiet_labels = [t["label"] for t in quiet_tenants]

    # ── Phase A: baseline — ONE quiet tenant gets traffic ─────────────
    # Manifest holds just the first quiet tenant so all 50 users land
    # there. Comparing p99 of THIS tenant in phase A to p99 of EACH
    # quiet tenant in phase B is the fairness check.
    baseline_label = quiet_labels[0]
    baseline_manifest = out_dir / "manifest_baseline.json"
    baseline_manifest.write_text(json.dumps([quiet_tenants[0]], indent=2))

    rc_a = _run_phase(
        phase="baseline",
        gateway_url=args.gateway_url, manifest_path=baseline_manifest,
        output_prefix=out_dir / "baseline_locust",
        users=int(args.quiet_users), spawn_rate=20, duration=args.duration,
    )
    print(f"[fairness:baseline] locust exit={rc_a}")
    baseline_per_tenant = parse_per_tenant_p99(out_dir / "baseline_locust_stats.csv")
    baseline_for_quiet = {
        # ALL quiet tenants in phase B compare against the single baseline
        # sample we captured here.
        lbl: baseline_per_tenant.get(baseline_label, 0.0)
        for lbl in quiet_labels
    }

    # ── Phase B: burst — all tenants live, noisy gets 500u, quiet 50u
    # The soak user distributes Locust users across manifest entries by
    # `index % len(manifest)`. To get the 4×50 + 1×500 split we repeat
    # each quiet tenant once and the noisy tenant 10 times — that way
    # 50/7 ≈ 14% of users land on each quiet tenant and the rest on
    # noisy. With 700 total users, that's ~50 per quiet and ~500 on
    # noisy.
    burst_manifest_entries = list(quiet_tenants) + (noisy_tenants * 10)
    burst_manifest = out_dir / "manifest_burst.json"
    burst_manifest.write_text(json.dumps(burst_manifest_entries, indent=2))

    burst_total_users = int(args.quiet_users) * int(args.quiet_tenant_count) + int(args.noisy_users)
    rc_b = _run_phase(
        phase="burst",
        gateway_url=args.gateway_url, manifest_path=burst_manifest,
        output_prefix=out_dir / "burst_locust",
        users=burst_total_users, spawn_rate=50, duration=args.duration,
    )
    print(f"[fairness:burst] locust exit={rc_b}")
    burst_per_tenant = parse_per_tenant_p99(out_dir / "burst_locust_stats.csv")

    # ── Compute fairness verdict ──────────────────────────────────────
    passed, report = compute_degradation(
        baseline=baseline_for_quiet,
        burst=burst_per_tenant,
        quiet_labels=quiet_labels,
        max_degradation_pct=float(args.max_degradation_pct),
    )
    final = {
        "timestamp":           ts,
        "gateway_url":         args.gateway_url,
        "duration_per_phase":  args.duration,
        "quiet_users":         args.quiet_users,
        "noisy_users":         args.noisy_users,
        "quiet_tenant_count":  args.quiet_tenant_count,
        "baseline_locust_exit": rc_a,
        "burst_locust_exit":    rc_b,
        "baseline_p99_for_quiet_tenant": baseline_per_tenant.get(baseline_label),
        "fairness":            report,
        "passed":              bool(rc_a == 0 and rc_b == 0 and passed),
    }
    (out_dir / "fairness_report.json").write_text(json.dumps(final, indent=2, sort_keys=True))

    if not args.no_teardown:
        _teardown_tenants(
            gateway_url=args.gateway_url, internal_secret=args.internal_secret,
            tenants=all_tenants,
        )

    print("=" * 70)
    for entry in report["per_tenant"]:
        print(f"  {entry}")
    print(f"  worst degradation: {report['worst_degradation_pct']}% (max {args.max_degradation_pct}%)")
    if final["passed"]:
        print("[fairness] ✓ PASSED — quiet tenants stayed within budget")
        return 0
    print("[fairness] ✗ FAILED — see fairness_report.json")
    return 1


if __name__ == "__main__":
    sys.exit(main())
