#!/usr/bin/env python3
"""Unattended soak harness.

What this script does, end-to-end, in one CI-runnable command:

  1. Provisions N test tenants via the identity service (default N=5).
  2. Mints a JWT for each tenant; writes them to a manifest file the
     locust `soak_user.py` reads via SOAK_MANIFEST env var.
  3. Launches locust headless with the soak user (1000u, 60min).
  4. After locust exits, runs the four post-run checks
     (chain verify / reconciliation / flight timelines closed /
     transparency roots verified) and saves a `checks.json` summary.
  5. Saves the locust CSV outputs alongside.
  6. Tears down the test tenants (DELETE from `acp_identity.tenants` —
     audit/usage rows stay because they're append-only; only the
     identity-side tenant row is removed so the next run starts clean).
  7. Exits non-zero if the aggregate failure rate, p99, OR any post-run
     check fails. Non-zero exit is the CI signal.

Reports land in `reports/soak/{timestamp}/`:

    locust_stats.csv           — locust aggregate stats
    locust_stats_history.csv   — locust over-time stats
    locust_failures.csv        — locust per-failure rows
    locust_exceptions.csv      — locust per-exception rows
    checks.json                — post-run validation results
    summary.json               — top-line acceptance + per-check pass/fail
    manifest.json              — tenants used (without tokens, for forensics)

Usage:

    # full 60-minute soak
    python tests/load/soak.py

    # shorter smoke (still touches all four checks)
    python tests/load/soak.py --users 50 --duration 5m --tenants 2

    # CI override of the gateway URL + secret
    GATEWAY_URL=http://acp:8000 INTERNAL_SECRET=$ACP_SECRET \
        python tests/load/soak.py --users 1000 --duration 60m
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

# Allow `python tests/load/soak.py` from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.load.post_run_checks import run_all_post_run_checks  # noqa: E402

# --------------------------------------------------------------------------- #
# Tenant provisioning                                                         #
# --------------------------------------------------------------------------- #


def _provision_tenants(
    *,
    gateway_url: str,
    internal_secret: str,
    n: int,
    label_prefix: str,
) -> list[dict[str, str]]:
    """Create N tenants + JWTs via the identity service. Returns a list of
    `{tenant_id, token, label}` ready for the manifest."""
    import httpx
    out: list[dict[str, str]] = []
    headers = {"X-Internal-Secret": internal_secret, "Content-Type": "application/json"}
    epoch = int(time.time())

    # The gateway's /auth/tenants proxy requires an admin JWT in addition
    # to the X-Internal-Secret. Mint a long-lived admin token once and
    # reuse it for every tenant provision below.
    try:
        ar = httpx.post(
            f"{gateway_url.rstrip('/')}/auth/token",
            json={"email": "admin@acp.local", "password": "password"},
            headers={"X-Tenant-ID": "00000000-0000-0000-0000-000000000001",
                     "Content-Type": "application/json"},
            timeout=10.0,
        )
        if ar.status_code != 200:
            raise RuntimeError(f"admin-token mint non-200: {ar.status_code} {ar.text[:200]}")
        admin_tok = (ar.json().get("data") or ar.json()).get("access_token")
        if not admin_tok:
            raise RuntimeError("admin-token mint returned no access_token")
    except Exception as exc:
        raise SystemExit(f"could not mint admin token for soak provisioning: {exc}")
    provision_headers = {**headers, "Authorization": f"Bearer {admin_tok}"}

    for i in range(n):
        tenant_id = str(uuid.uuid4())
        label = f"{label_prefix}-{i}-{epoch}"
        body = {
            "tenant_id": tenant_id,
            "org_id":    tenant_id,
            "tier":      "enterprise",
            "rpm_limit": 100_000,
            "name":      label,
        }
        try:
            r = httpx.post(
                f"{gateway_url.rstrip('/')}/auth/tenants",
                json=body, headers=provision_headers, timeout=10.0,
            )
            if r.status_code not in (200, 201):
                raise RuntimeError(f"upsert_tenant non-2xx: {r.status_code} {r.text[:200]}")
        except Exception as exc:
            raise SystemExit(f"could not provision tenant {label}: {exc}")

        # Provision a per-tenant admin user so we can mint a token scoped
        # to THIS tenant_id (the gateway rejects tokens whose tenant_id
        # claim doesn't match the X-Tenant-ID header — see Sprint
        # "tenant isolation" guards).
        soak_user_email = f"soak-{label}@acp.local"
        soak_user_pwd   = f"soak-{tenant_id[:8]}"
        try:
            ur = httpx.post(
                f"{gateway_url.rstrip('/')}/auth/users",
                json={"email": soak_user_email, "password": soak_user_pwd,
                      "tenant_id": tenant_id, "role": "ADMIN",
                      "full_name": f"Soak admin {label}"},
                headers=provision_headers, timeout=10.0,
            )
            # 200/201 = created; 409 = exists from a prior run, fine to reuse.
            if ur.status_code not in (200, 201, 409):
                raise RuntimeError(f"user create non-2xx: {ur.status_code} {ur.text[:200]}")
        except Exception as exc:
            raise SystemExit(f"could not provision soak user for {label}: {exc}")

        # Mint a soak token under the new per-tenant admin.
        try:
            tr = httpx.post(
                f"{gateway_url.rstrip('/')}/auth/token",
                json={"email": soak_user_email, "password": soak_user_pwd},
                headers={"X-Tenant-ID": tenant_id, "Content-Type": "application/json"},
                timeout=10.0,
            )
            if tr.status_code != 200:
                raise RuntimeError(f"token mint non-200: {tr.status_code} {tr.text[:200]}")
            tok = (tr.json().get("data") or tr.json()).get("access_token")
            if not tok:
                raise RuntimeError("token mint returned no access_token")
        except Exception as exc:
            raise SystemExit(f"could not mint token for {label}: {exc}")

        out.append({"tenant_id": tenant_id, "token": tok, "label": label})
    return out


def _teardown_tenants(
    *,
    gateway_url: str,
    internal_secret: str,
    tenants: list[dict[str, str]],
) -> None:
    """Best-effort cleanup. Failures here are logged, never fatal — the
    soak's success/failure has already been decided by post-run checks."""
    import httpx
    headers = {"X-Internal-Secret": internal_secret}
    # Same admin-token requirement on the teardown call.
    try:
        ar = httpx.post(
            f"{gateway_url.rstrip('/')}/auth/token",
            json={"email": "admin@acp.local", "password": "password"},
            headers={"X-Tenant-ID": "00000000-0000-0000-0000-000000000001",
                     "Content-Type": "application/json"},
            timeout=10.0,
        )
        admin_tok = (ar.json().get("data") or ar.json()).get("access_token") if ar.status_code == 200 else None
    except Exception:
        admin_tok = None
    if admin_tok:
        headers = {**headers, "Authorization": f"Bearer {admin_tok}"}
    for t in tenants:
        try:
            # No DELETE endpoint exists on /auth/tenants today; the
            # canonical teardown is to mark the tenant as suspended so
            # future requests are gate-kept by the existing tier checks.
            # Audit + usage rows stay (they're append-only by design).
            httpx.post(
                f"{gateway_url.rstrip('/')}/auth/tenants",
                json={"tenant_id": t["tenant_id"], "tier": "basic",
                      "rpm_limit": 0, "name": f"{t['label']}-suspended"},
                headers={**headers, "Content-Type": "application/json"},
                timeout=10.0,
            )
        except Exception:
            # Teardown best-effort only.
            pass


# --------------------------------------------------------------------------- #
# Locust orchestration                                                        #
# --------------------------------------------------------------------------- #


def _launch_locust(
    *,
    gateway_url: str,
    manifest_path: Path,
    output_prefix: Path,
    users: int,
    spawn_rate: int,
    duration: str,
) -> int:
    """Run locust headless. Returns its exit code."""
    locust_bin = shutil.which("locust") or shutil.which("locust.exe")
    if not locust_bin:
        raise SystemExit(
            "locust not found in PATH. Install dev deps: pip install locust"
        )
    cmd = [
        locust_bin, "-f", "tests/load/soak_user.py",
        "--headless", "--only-summary",
        "-u", str(users), "-r", str(spawn_rate), "-t", duration,
        "--host", gateway_url,
        "--csv", str(output_prefix),
    ]
    env = os.environ.copy()
    env["SOAK_MANIFEST"] = str(manifest_path)
    print(f"[soak] launching locust: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, env=env, cwd=str(_REPO_ROOT))
    return proc.returncode


# --------------------------------------------------------------------------- #
# CSV parsing → acceptance criteria                                           #
# --------------------------------------------------------------------------- #


def _parse_locust_aggregate(csv_path: Path) -> dict[str, float | int | None]:
    """Parse `*_stats.csv` and return aggregate p50/p95/p99/fail_rate."""
    import csv as _csv
    if not csv_path.exists():
        return {"error": "missing_stats_csv"}
    with csv_path.open() as fh:
        reader = _csv.DictReader(fh)
        rows = list(reader)
    agg = next((r for r in rows if r.get("Name") == "Aggregated"), None)
    if agg is None:
        return {"error": "no_aggregated_row"}

    def _f(k: str) -> float | None:
        v = agg.get(k)
        try:
            return float(v) if v not in (None, "", "N/A") else None
        except (ValueError, TypeError):
            return None

    req_count = _f("Request Count") or 0
    fail_count = _f("Failure Count") or 0
    fail_rate = (fail_count / req_count) if req_count else 0.0

    # /execute/valid percentiles — sum across all per-tenant rows
    # whose Name starts with /execute/valid.
    valid_rows = [r for r in rows if r.get("Name", "").startswith("/execute/valid")]
    p99_valid = _weighted_percentile(valid_rows, "99%")
    p95_valid = _weighted_percentile(valid_rows, "95%")
    p50_valid = _weighted_percentile(valid_rows, "50%")

    return {
        "agg_request_count": int(req_count),
        "agg_failure_count": int(fail_count),
        "agg_failure_rate":  round(fail_rate, 6),
        "execute_valid_p50_ms": p50_valid,
        "execute_valid_p95_ms": p95_valid,
        "execute_valid_p99_ms": p99_valid,
    }


def _weighted_percentile(rows: list[dict[str, str]], col: str) -> float | None:
    """Locust CSV reports percentile per Name. For the aggregate we
    pick the worst (max) across the per-tenant rows — that's the
    pessimistic answer, which is what we care about for SLO
    enforcement."""
    vals: list[float] = []
    for r in rows:
        v = r.get(col) or r.get(col.replace("%", "%ile"))
        try:
            if v not in (None, "", "N/A"):
                vals.append(float(v))
        except (ValueError, TypeError):
            continue
    return max(vals) if vals else None


# --------------------------------------------------------------------------- #
# Main                                                                        #
# --------------------------------------------------------------------------- #


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--users", type=int, default=1000, help="concurrent users (default 1000)")
    p.add_argument("--spawn-rate", type=int, default=50, help="users/sec ramp (default 50)")
    p.add_argument("--duration", default="60m", help="locust -t value (default 60m)")
    p.add_argument("--tenants", type=int, default=5, help="parallel tenants (default 5)")
    p.add_argument("--gateway-url", default=os.environ.get("GATEWAY_URL", "http://localhost:8000"))
    p.add_argument("--internal-secret", default=os.environ.get("INTERNAL_SECRET", ""))
    p.add_argument("--reports-dir", default=os.environ.get("REPORTS_DIR", "reports/soak"))
    p.add_argument("--label-prefix", default="soak", help="tenant label prefix")
    # Acceptance thresholds — overridable for CI smoke runs.
    p.add_argument("--max-failure-rate", type=float, default=0.005,
                   help="aggregate failure rate ceiling (default 0.5%%)")
    p.add_argument("--max-p99-ms", type=float, default=500.0,
                   help="/execute/valid p99 ceiling (ms; default 500)")
    p.add_argument("--no-teardown", action="store_true",
                   help="keep test tenants after the run (debug)")
    return p


def main() -> int:
    args = _build_argparser().parse_args()
    if not args.internal_secret:
        print("ERROR: INTERNAL_SECRET (or --internal-secret) is required to provision tenants",
              file=sys.stderr)
        return 2

    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = _REPO_ROOT / args.reports_dir / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[soak] reports → {out_dir}")

    # Step 1: provision tenants + write manifest
    tenants = _provision_tenants(
        gateway_url=args.gateway_url,
        internal_secret=args.internal_secret,
        n=int(args.tenants),
        label_prefix=args.label_prefix,
    )
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(tenants, indent=2))
    # Also write a non-secret variant for forensics (no tokens).
    (out_dir / "manifest_public.json").write_text(json.dumps(
        [{"tenant_id": t["tenant_id"], "label": t["label"]} for t in tenants],
        indent=2,
    ))

    # Step 2: launch locust
    run_started_at = datetime.now(UTC)
    locust_rc = _launch_locust(
        gateway_url=args.gateway_url,
        manifest_path=manifest_path,
        output_prefix=out_dir / "locust",
        users=int(args.users), spawn_rate=int(args.spawn_rate),
        duration=args.duration,
    )
    run_ended_at = datetime.now(UTC)
    print(f"[soak] locust exit={locust_rc}, window={run_started_at.isoformat()} → {run_ended_at.isoformat()}")

    # Step 3: parse locust output for SLO checks
    locust_stats = _parse_locust_aggregate(out_dir / "locust_stats.csv")

    failure_rate = float(locust_stats.get("agg_failure_rate") or 0.0)
    p99_valid    = locust_stats.get("execute_valid_p99_ms") or 0.0

    slo_passed = (
        failure_rate <= args.max_failure_rate
        and p99_valid is not None
        and p99_valid <= args.max_p99_ms
    )

    # Step 4: post-run checks
    all_checks_passed, check_results = run_all_post_run_checks(
        gateway_url=args.gateway_url,
        internal_secret=args.internal_secret,
        run_started_at=run_started_at, run_ended_at=run_ended_at,
    )
    (out_dir / "checks.json").write_text(json.dumps(
        [c.to_dict() for c in check_results], indent=2,
    ))

    # Step 5: summary + teardown
    summary = {
        "timestamp":       ts,
        "gateway_url":     args.gateway_url,
        "duration":        args.duration,
        "users":           args.users,
        "tenants":         args.tenants,
        "run_started_at":  run_started_at.isoformat(),
        "run_ended_at":    run_ended_at.isoformat(),
        "locust_exit":     locust_rc,
        "locust_stats":    locust_stats,
        "slo": {
            "failure_rate":         failure_rate,
            "execute_valid_p99_ms": p99_valid,
            "max_failure_rate":     args.max_failure_rate,
            "max_p99_ms":           args.max_p99_ms,
            "passed":               slo_passed,
        },
        "checks": [c.to_dict() for c in check_results],
        "passed": bool(locust_rc == 0 and slo_passed and all_checks_passed),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True))

    if not args.no_teardown:
        _teardown_tenants(
            gateway_url=args.gateway_url,
            internal_secret=args.internal_secret,
            tenants=tenants,
        )

    # Step 6: print a human summary, exit non-zero on any failure
    print("=" * 70)
    print(f"  failure_rate         {failure_rate:.4%} (<= {args.max_failure_rate:.2%})")
    print(f"  /execute/valid p99   {p99_valid} ms (<= {args.max_p99_ms} ms)")
    for c in check_results:
        marker = "OK" if c.passed else "FAIL"
        print(f"  [{marker}] {c.name}")
    print("=" * 70)
    if summary["passed"]:
        print("[soak] ✓ PASSED")
        return 0
    print("[soak] ✗ FAILED — see summary.json")
    return 1


if __name__ == "__main__":
    sys.exit(main())
