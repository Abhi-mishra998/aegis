#!/usr/bin/env python3
"""
Real Traffic Generator — produces genuine ACP governance decisions (no seeded data).

Unlike seed_demo_data.py which backfills fake rows, this script makes actual
POST /execute calls against the running ACP gateway.  Every decision is real:
governed by OPA, scored by the behavior engine, and written to the tamper-evident
audit chain.

Usage:
    # Against local stack (docker-compose up):
    python scripts/generate_real_traffic.py --host http://localhost:8000 --rounds 5

    # Against production:
    python scripts/generate_real_traffic.py \
        --host https://aegisagent.in \
        --token <JWT> \
        --rounds 20 \
        --concurrency 4

    # Quiet mode (no per-decision output — just final stats):
    python scripts/generate_real_traffic.py --quiet
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_HOST      = os.environ.get("ACP_GATEWAY_URL", "http://localhost:8000")
DEFAULT_TENANT_ID = os.environ.get("ACP_TENANT_ID",   "00000000-0000-0000-0000-000000000001")
DEFAULT_AGENT_ID  = os.environ.get("ACP_AGENT_ID",    "11111111-1111-1111-1111-111111111111")

# ── Traffic mix ───────────────────────────────────────────────────────────────
# Realistic enterprise workload: mostly safe reads, occasional risky writes,
# rare destructive operations.  Mirrors the locust test distribution.

TRAFFIC_MIX: list[dict] = [
    # Safe operations (60% of traffic) — should be ALLOWED
    {"tool": "read_file",   "params": {"path": "/data/reports/q1_summary.csv"},           "label": "safe_read",      "weight": 15},
    {"tool": "read_file",   "params": {"path": "/app/config/settings.json"},               "label": "safe_read",      "weight": 10},
    {"tool": "list_dir",    "params": {"path": "/data/reports"},                            "label": "safe_list",      "weight": 10},
    {"tool": "http_request","params": {"url": "https://api.github.com/", "method": "GET"}, "label": "safe_http",      "weight": 10},
    {"tool": "run_sql",     "params": {"query": "SELECT count(*) FROM audit_logs"},         "label": "safe_sql_read",  "weight": 15},

    # Risky operations (30% of traffic) — may be MONITORED or ESCALATED
    {"tool": "send_email",  "params": {"to": "team@company.com", "subject": "Update", "body": "System update"}, "label": "risky_email",   "weight": 10},
    {"tool": "run_sql",     "params": {"query": "SELECT * FROM users LIMIT 100"},           "label": "risky_sql_all", "weight": 10},
    {"tool": "http_request","params": {"url": "https://internal.company.com/api/keys", "method": "GET"}, "label": "risky_internal", "weight": 10},

    # Dangerous operations (10% of traffic) — should be BLOCKED
    {"tool": "read_file",   "params": {"path": "/etc/passwd"},                              "label": "dangerous_sensitive", "weight": 3},
    {"tool": "run_sql",     "params": {"query": "DELETE FROM users WHERE id > 0"},          "label": "dangerous_sql",       "weight": 3},
    {"tool": "read_file",   "params": {"path": "/etc/ssl/private/server.key"},              "label": "dangerous_key",       "weight": 2},
    {"tool": "run_sql",     "params": {"query": "DROP TABLE temp_sessions"},                "label": "dangerous_ddl",       "weight": 2},
]

_TOOLS_POPULATION = []
for entry in TRAFFIC_MIX:
    _TOOLS_POPULATION.extend([entry] * entry["weight"])


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _extract_tenant_from_jwt(token: str) -> str:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part)).get("tenant_id", "")
    except Exception:
        return ""


def _get_token(host: str, email: str, password: str, tenant_id: str) -> tuple[str, str] | None:
    try:
        r = httpx.post(
            f"{host}/auth/token",
            json={"email": email, "password": password},
            headers={"X-Tenant-ID": tenant_id},
            timeout=10.0,
        )
        if r.status_code == 200:
            d = r.json().get("data", {})
            tok = d.get("access_token", "")
            tid = d.get("tenant_id", tenant_id)
            return tok, tid
    except Exception as e:
        print(f"[auth] token generation failed: {e}", file=sys.stderr)
    return None


# ── Core execute call ─────────────────────────────────────────────────────────

def execute_one(host: str, token: str, tenant_id: str, agent_id: str, entry: dict, quiet: bool) -> dict:
    request_id = str(uuid.uuid4())
    headers = {
        "Authorization":  f"Bearer {token}",
        "X-Tenant-ID":    tenant_id,
        "X-Agent-ID":     agent_id,
        "X-Request-ID":   request_id,
        "Content-Type":   "application/json",
    }
    payload = {
        "agent_id":   agent_id,
        "tool_name":  entry["tool"],
        "parameters": entry["params"],
        "context":    {"label": entry["label"], "generated_by": "generate_real_traffic.py"},
    }
    try:
        r = httpx.post(f"{host}/execute", headers=headers, json=payload, timeout=15.0)
        status = r.status_code
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        action = body.get("action") or body.get("data", {}).get("action") or ("blocked" if status == 403 else "unknown")
        risk   = body.get("risk_score") or body.get("data", {}).get("risk_score") or 0.0
        result = {"tool": entry["tool"], "label": entry["label"], "status": status, "action": action, "risk": risk, "request_id": request_id}
        if not quiet:
            icon = "✅" if action in ("allow", "allowed") else "🚫" if action in ("deny", "block", "blocked", "policy_deny") else "⚠️"
            print(f"  {icon}  {entry['tool']:<18}  {action:<14}  risk={float(risk):.2f}  [{status}]")
        return result
    except Exception as exc:
        if not quiet:
            print(f"  ❌  {entry['tool']:<18}  error: {exc}")
        return {"tool": entry["tool"], "label": entry["label"], "status": 0, "action": "error", "risk": 0.0, "error": str(exc)}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate real ACP governance traffic")
    ap.add_argument("--host",        default=DEFAULT_HOST,      help="ACP gateway URL")
    ap.add_argument("--token",       default="",                help="JWT (skips auto-login)")
    ap.add_argument("--email",       default="admin@acp.local", help="Login email for auto-login")
    ap.add_argument("--password",    default="password",        help="Login password for auto-login")
    ap.add_argument("--tenant-id",   default=DEFAULT_TENANT_ID, help="Tenant UUID")
    ap.add_argument("--agent-id",    default=DEFAULT_AGENT_ID,  help="Agent UUID")
    ap.add_argument("--rounds",      type=int, default=3,       help="Number of full traffic rounds")
    ap.add_argument("--concurrency", type=int, default=2,       help="Parallel workers")
    ap.add_argument("--delay",       type=float, default=0.3,   help="Delay between rounds (seconds)")
    ap.add_argument("--quiet",       action="store_true",       help="Suppress per-decision output")
    ap.add_argument("--seed", type=int, default=None,           help="Random seed for reproducibility")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    host      = args.host.rstrip("/")
    token     = args.token
    tenant_id = args.tenant_id
    agent_id  = args.agent_id

    # Auto-login if no token provided
    if not token:
        if not args.quiet:
            print(f"[auth] logging in as {args.email} …")
        result = _get_token(host, args.email, args.password, tenant_id)
        if not result:
            print("ERROR: could not obtain auth token. Pass --token or check credentials.", file=sys.stderr)
            sys.exit(1)
        token, tenant_id = result
        if not args.quiet:
            print(f"[auth] ✓ token obtained (tenant={tenant_id})")
    else:
        extracted = _extract_tenant_from_jwt(token)
        if extracted:
            tenant_id = extracted

    if not args.quiet:
        print(f"\n{'='*60}")
        print("Aegis Real Traffic Generator")
        print(f"Host:    {host}")
        print(f"Tenant:  {tenant_id}")
        print(f"Agent:   {agent_id}")
        print(f"Rounds:  {args.rounds} × {len(TRAFFIC_MIX)} calls = {args.rounds * len(TRAFFIC_MIX)} total")
        print(f"Workers: {args.concurrency}")
        print(f"{'='*60}\n")

    # Build call list
    calls: list[dict] = []
    for _ in range(args.rounds):
        batch = random.choices(_TOOLS_POPULATION, k=len(TRAFFIC_MIX))
        calls.extend(batch)

    random.shuffle(calls)

    # Execute
    start = time.time()
    results: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(execute_one, host, token, tenant_id, agent_id, call, args.quiet)
            for call in calls
        ]
        for i, fut in enumerate(as_completed(futures), 1):
            results.append(fut.result())
            if not args.quiet and i % 10 == 0:
                print(f"  … {i}/{len(calls)} completed")
            if args.delay > 0 and i % args.concurrency == 0:
                time.sleep(args.delay)

    elapsed = time.time() - start

    # Stats
    total    = len(results)
    allowed  = sum(1 for r in results if r["action"] in ("allow", "allowed"))
    blocked  = sum(1 for r in results if r["action"] in ("deny", "block", "blocked", "policy_deny"))
    errors   = sum(1 for r in results if r["action"] == "error")
    other    = total - allowed - blocked - errors
    avg_risk = sum(float(r.get("risk", 0)) for r in results) / max(total, 1)

    print(f"\n{'─'*60}")
    print(f"Results ({elapsed:.1f}s, {total / max(elapsed, 0.001):.1f} req/s):")
    print(f"  Total decisions : {total}")
    print(f"  Allowed         : {allowed}  ({100 * allowed / max(total, 1):.0f}%)")
    print(f"  Blocked         : {blocked}  ({100 * blocked / max(total, 1):.0f}%)")
    print(f"  Other           : {other}")
    print(f"  Errors          : {errors}")
    print(f"  Avg risk score  : {avg_risk:.3f}")
    print(f"{'─'*60}")
    print("  All decisions are live in the audit trail.")
    print(f"  View at: {host}/observability")
    print()

    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
