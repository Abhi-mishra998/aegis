"""Post-run validation for the soak + fairness harnesses.

Every function returns ``(passed: bool, detail: dict)``. The detail
dict is what gets serialised into reports/soak/{timestamp}/checks.json
so an operator can read why a check failed without re-running.

The functions are deliberately small + pure-ish (HTTP/DB calls are
the only side effects). Mock them in `tests/test_soak_post_run.py`.

Checks performed:

    chain_verify           — /audit/logs/verify is_integrous=true,
                             violations=0
    reconciliation         — scripts/ops/reconcile.py status=VERIFIED
                             AND zero gap in either direction
    flight_timelines       — every execution_timelines row for the run
                             window has status != "in_progress" 60s
                             after the run ended
    transparency_roots     — ≥1 transparency root with root_date inside
                             the run window AND verify_root=true via
                             /transparency/verify-root

The soak orchestrator runs them in this order and stops on the first
failure (post-run is fast — chain verify is the only DB-heavy one).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

# httpx is in the dev venv; tolerate its absence for static-only test envs.
try:
    import httpx  # type: ignore
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore


# --------------------------------------------------------------------------- #
# Result type                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# /audit/logs/verify                                                          #
# --------------------------------------------------------------------------- #


def check_chain_verify(*, gateway_url: str, internal_secret: str | None = None) -> CheckResult:
    """Hit /audit/logs/verify and require is_integrous=true, violations=0."""
    if httpx is None:
        return CheckResult("chain_verify", False, {"error": "httpx unavailable"})
    headers: dict[str, str] = {}
    if internal_secret:
        headers["X-Internal-Secret"] = internal_secret
    # The gateway's proxies require a JWT in addition to X-Internal-Secret.
    # Sprint 3.6 harness fix: post-run checks mint a short-lived admin
    # token at first use so the upstream proxies authenticate cleanly.
    _attach_admin_jwt(headers, gateway_url=gateway_url)
    try:
        resp = httpx.get(
            f"{gateway_url.rstrip('/')}/audit/logs/verify",
            headers=headers, timeout=30.0,
        )
    except Exception as exc:
        return CheckResult("chain_verify", False, {"error": f"http_error:{exc}"})

    if resp.status_code != 200:
        return CheckResult("chain_verify", False, {
            "error": "non_200", "status_code": resp.status_code,
            "body": resp.text[:300],
        })

    body = _safe_json(resp)
    data = body.get("data", body)
    is_integrous = bool(data.get("is_integrous"))
    violations = int(data.get("violations") or 0)
    processed = int(data.get("processed_count") or 0)

    return CheckResult(
        "chain_verify",
        passed=(is_integrous and violations == 0),
        detail={
            "is_integrous":    is_integrous,
            "violations":      violations,
            "processed_count": processed,
        },
    )


# --------------------------------------------------------------------------- #
# scripts/ops/reconcile.py                                                    #
# --------------------------------------------------------------------------- #


_DEFAULT_AUDIT_DB = "postgresql://postgres:postgres@localhost:5433/acp_audit"
_DEFAULT_USAGE_DB = "postgresql://postgres:postgres@localhost:5433/acp_usage"


def check_reconciliation(
    *,
    audit_db: str | None = None,
    usage_db: str | None = None,
    grace_seconds: int = 60,
    timeout_seconds: int = 60,
) -> CheckResult:
    """Run the reconcile CLI in --json mode and parse the report.

    We exec the CLI as a subprocess (rather than importing it) because
    the script's own connection / argparse code is the contract we
    promise operators — running it the same way they would catches
    regressions in the CLI surface, not just the pure-function core.
    """
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cmd = [sys.executable, "scripts/ops/reconcile.py",
           "--json", "--grace-seconds", str(grace_seconds)]
    env = os.environ.copy()
    # Default to the local docker postgres exposed on host port 5433 so
    # the post-run check works out-of-the-box for the soak harness run
    # from the repo root. Callers can override via env or the kwargs.
    env.setdefault("ACP_AUDIT_DB", _DEFAULT_AUDIT_DB)
    env.setdefault("ACP_USAGE_DB", _DEFAULT_USAGE_DB)
    if audit_db:
        env["ACP_AUDIT_DB"] = audit_db
    if usage_db:
        env["ACP_USAGE_DB"] = usage_db

    try:
        proc = subprocess.run(
            cmd, cwd=repo_root, env=env, capture_output=True, text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return CheckResult("reconciliation", False, {"error": f"exec_error:{exc}"})

    out = proc.stdout.strip() or "{}"
    try:
        report = json.loads(out)
    except Exception:
        return CheckResult("reconciliation", False, {
            "error": "parse_error",
            "exit_code": proc.returncode,
            "stdout": out[:300],
            "stderr": proc.stderr[:300],
        })

    # The CLI exit code is authoritative; the parsed body gives detail.
    return CheckResult(
        "reconciliation",
        passed=(proc.returncode == 0 and report.get("status") == "VERIFIED"),
        detail={
            "status":                    report.get("status"),
            "audit_without_usage_count": report.get("audit_without_usage_count"),
            "usage_without_audit_count": report.get("usage_without_audit_count"),
            "billing_dlq_length":        report.get("billing_dlq_length"),
            "exit_code":                 proc.returncode,
        },
    )


# --------------------------------------------------------------------------- #
# Flight Recorder leaked timelines                                            #
# --------------------------------------------------------------------------- #


def check_flight_timelines_closed(
    *,
    gateway_url: str,
    internal_secret: str | None = None,
    run_started_at: datetime,
    run_ended_at:   datetime,
    settle_seconds: int = 60,
) -> CheckResult:
    """Allow `settle_seconds` for the worker to drain, then assert no
    timeline created during the run window is still `in_progress`.

    Uses /flight/timelines through the gateway proxy (the same path
    the UI walks). Pagination: walks until the oldest row in a page
    is older than `run_started_at` so we cap memory under long runs.
    """
    if httpx is None:
        return CheckResult("flight_timelines_closed", False, {"error": "httpx unavailable"})

    # Settle window — the flight worker is async (Sprint 1.2). Without
    # this delay we'd catch timelines that ARE going to close in a few
    # hundred ms but haven't yet.
    time.sleep(max(0, int(settle_seconds)))

    headers: dict[str, str] = {}
    if internal_secret:
        headers["X-Internal-Secret"] = internal_secret
    # The gateway's proxies require a JWT in addition to X-Internal-Secret.
    # Sprint 3.6 harness fix: post-run checks mint a short-lived admin
    # token at first use so the upstream proxies authenticate cleanly.
    _attach_admin_jwt(headers, gateway_url=gateway_url)

    leaked: list[dict[str, Any]] = []
    cursor: str | None = None
    pages_walked = 0
    max_pages = 200  # belt + braces; per-page is 50 → 10k rows max

    while pages_walked < max_pages:
        url = f"{gateway_url.rstrip('/')}/flight/timelines?limit=50"
        if cursor:
            url += f"&before={cursor}"
        try:
            resp = httpx.get(url, headers=headers, timeout=10.0)
        except Exception as exc:
            return CheckResult("flight_timelines_closed", False,
                               {"error": f"http_error:{exc}"})
        if resp.status_code != 200:
            return CheckResult("flight_timelines_closed", False, {
                "error": "non_200", "status_code": resp.status_code,
            })
        body = _safe_json(resp)
        rows = (body.get("data") if isinstance(body, dict) else None) or []
        if not rows:
            break

        run_started_at_aware = _as_aware(run_started_at)
        run_ended_at_aware = _as_aware(run_ended_at)

        for row in rows:
            started = _parse_iso(row.get("started_at"))
            if started is None:
                continue
            if started < run_started_at_aware:
                # We've walked past the run window; older pages can be skipped.
                return _flight_result(leaked)
            if started > run_ended_at_aware:
                continue
            if (row.get("status") or "").lower() == "in_progress":
                leaked.append({
                    "id":          row.get("id"),
                    "tool":        row.get("tool"),
                    "started_at":  row.get("started_at"),
                    "request_id":  row.get("request_id"),
                })
        # Advance cursor to the oldest row on this page. If the upstream
        # endpoint doesn't honour the cursor (returns the same page
        # forever), break rather than enter an O(max_pages) replay loop.
        next_cursor = rows[-1].get("started_at")
        if next_cursor == cursor:
            break
        cursor = next_cursor
        pages_walked += 1

    return _flight_result(leaked)


def _flight_result(leaked: list[dict[str, Any]]) -> CheckResult:
    return CheckResult(
        "flight_timelines_closed",
        passed=(len(leaked) == 0),
        detail={
            "leaked_count":  len(leaked),
            "leaked_sample": leaked[:10],
        },
    )


# --------------------------------------------------------------------------- #
# Transparency roots covering the run window                                  #
# --------------------------------------------------------------------------- #


def check_transparency_roots(
    *,
    gateway_url: str,
    internal_secret: str | None = None,
    run_started_at: datetime,
    run_ended_at:   datetime,
) -> CheckResult:
    """Require ≥1 transparency root with `root_date` inside the run window,
    and that EVERY such root passes /transparency/verify-root."""
    if httpx is None:
        return CheckResult("transparency_roots", False, {"error": "httpx unavailable"})

    since_date = _as_aware(run_started_at).date().isoformat()
    until_date = _as_aware(run_ended_at).date().isoformat()
    headers: dict[str, str] = {}
    if internal_secret:
        headers["X-Internal-Secret"] = internal_secret
    # The gateway's proxies require a JWT in addition to X-Internal-Secret.
    # Sprint 3.6 harness fix: post-run checks mint a short-lived admin
    # token at first use so the upstream proxies authenticate cleanly.
    _attach_admin_jwt(headers, gateway_url=gateway_url)

    try:
        list_resp = httpx.get(
            f"{gateway_url.rstrip('/')}/transparency/roots"
            f"?since={since_date}&until={until_date}&limit=14",
            headers=headers, timeout=15.0,
        )
    except Exception as exc:
        return CheckResult("transparency_roots", False, {"error": f"http_error:{exc}"})

    if list_resp.status_code != 200:
        return CheckResult("transparency_roots", False, {
            "error": "non_200_list", "status_code": list_resp.status_code,
        })
    roots = (_safe_json(list_resp).get("data") or [])
    if not roots:
        return CheckResult("transparency_roots", False, {
            "error":      "no_roots_in_window",
            "since":      since_date,
            "until":      until_date,
        })

    # Verify each root via /transparency/verify-root. The endpoint returns
    # the canonical {valid, algorithm, expected_fingerprint, errors} shape
    # (Sprint 1.3) — any errors field non-empty means a chain or signature
    # break that the operator must investigate.
    verified: list[dict[str, Any]] = []
    for r in roots:
        signed = r.get("signed")
        if not isinstance(signed, dict):
            verified.append({"root_date": r.get("root_date"),
                             "valid": False, "errors": ["malformed_payload"]})
            continue
        try:
            vr = httpx.post(
                f"{gateway_url.rstrip('/')}/transparency/verify-root",
                headers=headers, json=signed, timeout=10.0,
            )
        except Exception as exc:
            verified.append({"root_date": r.get("root_date"),
                             "valid": False, "errors": [f"http_error:{exc}"]})
            continue
        body = _safe_json(vr).get("data") or {}
        verified.append({
            "root_date": r.get("root_date"),
            "valid":     bool(body.get("valid")),
            "errors":    list(body.get("errors") or []),
        })

    all_valid = bool(verified) and all(v["valid"] for v in verified)
    return CheckResult(
        "transparency_roots",
        passed=all_valid,
        detail={
            "roots_in_window":     len(roots),
            "verifications":       verified,
        },
    )


# --------------------------------------------------------------------------- #
# Aggregate                                                                   #
# --------------------------------------------------------------------------- #


def run_all_post_run_checks(
    *,
    gateway_url: str,
    internal_secret: str | None,
    run_started_at: datetime,
    run_ended_at:   datetime,
    audit_db: str | None = None,
    usage_db: str | None = None,
    settle_seconds: int = 60,
) -> tuple[bool, list[CheckResult]]:
    """Run every check; return (all_passed, results_in_order)."""
    results: list[CheckResult] = [
        check_chain_verify(gateway_url=gateway_url, internal_secret=internal_secret),
        check_reconciliation(audit_db=audit_db, usage_db=usage_db),
        check_flight_timelines_closed(
            gateway_url=gateway_url, internal_secret=internal_secret,
            run_started_at=run_started_at, run_ended_at=run_ended_at,
            settle_seconds=settle_seconds,
        ),
        check_transparency_roots(
            gateway_url=gateway_url, internal_secret=internal_secret,
            run_started_at=run_started_at, run_ended_at=run_ended_at,
        ),
    ]
    return all(r.passed for r in results), results


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


_ADMIN_TOKEN_CACHE: dict[str, str] = {}
_DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"


def _attach_admin_jwt(headers: dict[str, str], *, gateway_url: str) -> None:
    """Mint (and cache) an admin JWT for the default tenant so each
    post-run probe authenticates through the gateway proxy.

    The cache key is `gateway_url`; one token per harness run. If the
    mint fails (e.g. seed_admin.py wasn't run) we silently leave the
    Authorization header off — the check will return its native non-200
    detail and the harness exit code stays meaningful.
    """
    if httpx is None:
        return
    if "Authorization" in headers:
        return
    cached = _ADMIN_TOKEN_CACHE.get(gateway_url)
    if cached:
        headers["Authorization"] = f"Bearer {cached}"
        headers.setdefault("X-Tenant-ID", _DEFAULT_TENANT)
        return
    try:
        resp = httpx.post(
            f"{gateway_url.rstrip('/')}/auth/token",
            json={"email": "admin@acp.local", "password": "password"},
            headers={"X-Tenant-ID": _DEFAULT_TENANT, "Content-Type": "application/json"},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return
        body = resp.json()
        tok = (body.get("data") or body).get("access_token")
        if not tok:
            return
        _ADMIN_TOKEN_CACHE[gateway_url] = tok
        headers["Authorization"] = f"Bearer {tok}"
        headers.setdefault("X-Tenant-ID", _DEFAULT_TENANT)
    except Exception:
        pass


def _safe_json(resp: Any) -> dict[str, Any]:
    try:
        out = resp.json()
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def _parse_iso(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
