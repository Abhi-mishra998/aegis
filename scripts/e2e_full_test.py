#!/usr/bin/env python3
"""
ACP Full System E2E Test
========================
Tests every major endpoint against the live production stack.
Run: python3 scripts/e2e_full_test.py [BASE_URL]
"""
from __future__ import annotations

import json
import sys
import time
import uuid
import os
from datetime import UTC, date, datetime, timedelta

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else os.getenv("ACP_BASE_URL", "https://aegisagent.in")
TENANT_ID = "00000000-0000-0000-0000-000000000001"
ADMIN_EMAIL = "admin@acp.local"
ADMIN_PASSWORD = "password"

PASS = FAIL = 0
_results: list[tuple[str, bool, str]] = []

def ok(label: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    _results.append((label, True, detail))
    print(f"  PASS  {label}" + (f"  [{detail}]" if detail else ""))

def fail(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    _results.append((label, False, detail))
    print(f"  FAIL  {label}" + (f"  [{detail[:120]}]" if detail else ""))

def check(label: str, cond: bool, detail: str = "") -> bool:
    if cond:
        ok(label, detail)
    else:
        fail(label, detail)
    return cond

def get(c: httpx.Client, path: str, **kw) -> httpx.Response:
    return c.get(f"{BASE_URL}{path}", **kw)

def post(c: httpx.Client, path: str, **kw) -> httpx.Response:
    return c.post(f"{BASE_URL}{path}", **kw)


def run():
    with httpx.Client(timeout=30, verify=True) as c:

        # ── 1. HEALTH / STATUS ──────────────────────────────────────────────
        print("\n[1] Infrastructure Health")
        r = get(c, "/health")
        check("GET /health → 200", r.status_code == 200, r.text[:80])

        r = get(c, "/status")
        check("GET /status → 200", r.status_code == 200)
        if r.status_code == 200:
            d = r.json()
            check("status.status = operational", d.get("status") == "operational", d.get("status", ""))
            svcs = d.get("services", {})
            total = svcs.get("total", 0)
            healthy = svcs.get("healthy", 0)
            check(f"all {total} services healthy", total == healthy == 12, f"healthy={healthy}/{total}")
            queues = d.get("queues", {})
            audit_backlog = queues.get("audit_stream_length", 0)
            check("audit_stream_backlog < 50000 (MAXLEN)", audit_backlog < 50_000, f"backlog={audit_backlog}")

        r = get(c, "/system/health")
        check("GET /system/health → 200", r.status_code == 200)

        # ── 2. AUTH ─────────────────────────────────────────────────────────
        print("\n[2] Authentication")
        r = post(c, "/auth/token",
                 json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                 headers={"X-Tenant-ID": TENANT_ID})
        check("POST /auth/token → 200", r.status_code == 200, r.text[:80])
        user_token = ""
        if r.status_code == 200:
            d = r.json()
            user_token = d.get("data", {}).get("access_token", "")
            check("access_token present", bool(user_token), user_token[:40] if user_token else "missing")

        if not user_token:
            print("  ABORT: no user token, remaining tests will fail")
            return

        auth = {"Authorization": f"Bearer {user_token}", "X-Tenant-ID": TENANT_ID}

        # Wrong password → 401
        r = post(c, "/auth/token",
                 json={"email": ADMIN_EMAIL, "password": "wrongpassword"},
                 headers={"X-Tenant-ID": TENANT_ID})
        check("POST /auth/token wrong password → 401", r.status_code == 401, r.text[:60])

        # ── 3. REGISTRY ─────────────────────────────────────────────────────
        print("\n[3] Agent Registry")
        r = get(c, "/agents", headers=auth)
        check("GET /agents → 200", r.status_code == 200, r.text[:80])
        existing_agent_id = ""
        if r.status_code == 200:
            d = r.json()
            raw = d.get("data", {}) or {}
            agent_list = (raw.get("items") or raw.get("data") or
                          (raw if isinstance(raw, list) else []))
            check("agents list non-empty", len(agent_list) > 0, f"count={len(agent_list)}")
            if agent_list:
                existing_agent_id = agent_list[0].get("id", "")

        # Create a fresh test agent
        ts = int(time.time())
        agent_name = f"e2e-test-{ts}"
        r = post(c, "/agents",
                 json={"name": agent_name, "description": "E2E test agent", "owner_id": "e2e"},
                 headers=auth)
        check("POST /agents (create) → 201", r.status_code == 201, r.text[:80])
        new_agent_id = ""
        if r.status_code == 201:
            new_agent_id = r.json().get("data", {}).get("id", "")
            check("new agent has id", bool(new_agent_id), new_agent_id)

        # GET the new agent
        if new_agent_id:
            r = get(c, f"/agents/{new_agent_id}", headers=auth)
            check("GET /agents/{id} → 200", r.status_code == 200, r.text[:80])

        # ── 4. AGENT TOKEN ───────────────────────────────────────────────────
        print("\n[4] Agent Credentials & Token")
        agent_token = ""
        if new_agent_id:
            secret = f"e2e-secret-{ts}"
            r = post(c, "/auth/credentials",
                     json={"agent_id": new_agent_id, "secret": secret},
                     headers=auth)
            check("POST /auth/credentials → 201", r.status_code == 201, r.text[:80])

            r = post(c, "/auth/agent/token",
                     json={"agent_id": new_agent_id, "secret": secret},
                     headers={"X-Tenant-ID": TENANT_ID})
            check("POST /auth/agent/token → 200", r.status_code == 200, r.text[:80])
            if r.status_code == 200:
                agent_token = r.json().get("data", {}).get("access_token", "")
                check("agent token present", bool(agent_token), agent_token[:40] if agent_token else "missing")

        # ── 5. PERMISSIONS / RBAC ────────────────────────────────────────────
        print("\n[5] RBAC / Permissions")
        perm_id = ""
        if new_agent_id:
            # Add a permission
            r = post(c, f"/agents/{new_agent_id}/permissions",
                     json={"tool_name": "read_file", "action": "ALLOW"},
                     headers=auth)
            check("POST /agents/{id}/permissions (add) → 201", r.status_code == 201, r.text[:80])
            perm_id = r.json().get("data", {}).get("id", "") if r.status_code == 201 else ""

            # List permissions
            r = get(c, f"/agents/{new_agent_id}/permissions", headers=auth)
            check("GET /agents/{id}/permissions → 200", r.status_code == 200, r.text[:80])
            if r.status_code == 200:
                perms = r.json().get("data", [])
                check("permission list non-empty", len(perms) > 0, f"count={len(perms)}")

            # Revoke it
            if perm_id:
                r = c.delete(f"{BASE_URL}/agents/{new_agent_id}/permissions/{perm_id}", headers=auth)
                check("DELETE /agents/{id}/permissions/{pid} → 200/204",
                      r.status_code in (200, 204), r.text[:60])

        # ── 6. PRE-EXECUTE: ADD REQUIRED PERMISSIONS ─────────────────────────
        # Security checks (SQL injection, path traversal) fire AFTER policy check.
        # The agent needs explicit permissions so policy passes and security checks run.
        exec_agent_id = new_agent_id or existing_agent_id
        if exec_agent_id:
            for tool in ["list_files", "read_file", "query_db", "execute_command"]:
                r = post(c, f"/agents/{exec_agent_id}/permissions",
                         json={"tool_name": tool, "action": "ALLOW"},
                         headers=auth)

        exec_headers = {**auth, "X-Agent-ID": exec_agent_id}

        # ── 7. EXECUTE — NORMAL ──────────────────────────────────────────────
        print("\n[6] Execute — Normal Request (expect allow)")
        r = post(c, "/execute",
                 json={"tool": "list_files", "payload": {"path": "/tmp"},
                       "agent_id": exec_agent_id},
                 headers=exec_headers)
        check("POST /execute (normal) → 200", r.status_code == 200, r.text[:100])
        if r.status_code == 200:
            d = r.json().get("data", {}) or r.json()
            action = d.get("action", "")
            risk = float(d.get("risk", -1))
            findings = d.get("findings", [])
            check("normal execute → allow", action == "allow", f"action={action} risk={risk:.3f}")
            check("normal execute → no anomalous_behavior in findings",
                  "anomalous_behavior_detected" not in findings,
                  f"findings={findings}")
            check("normal execute risk < 0.60", risk < 0.60, f"risk={risk:.3f}")

        # ── 8. EXECUTE — SQL INJECTION ───────────────────────────────────────
        print("\n[7] Execute — SQL Injection (expect deny)")
        r = post(c, "/execute",
                 json={"tool": "query_db",
                       "payload": {"query": "SELECT * FROM users; DROP TABLE users; --"},
                       "agent_id": exec_agent_id},
                 headers=exec_headers)
        check("POST /execute SQL inject → 403", r.status_code == 403, r.text[:100])
        if r.status_code == 403:
            body = r.json()
            body_str = json.dumps(body).lower()
            check("SQL inject finding present",
                  "sql" in body_str or "injection" in body_str or "drop" in body_str,
                  str(body)[:80])

        # ── 9. EXECUTE — PATH TRAVERSAL ──────────────────────────────────────
        print("\n[8] Execute — Path Traversal (expect deny)")
        r = post(c, "/execute",
                 json={"tool": "read_file",
                       "payload": {"path": "../../etc/passwd"},
                       "agent_id": exec_agent_id},
                 headers=exec_headers)
        check("POST /execute path traversal → 403", r.status_code == 403, r.text[:100])
        if r.status_code == 403:
            body = r.json()
            body_str = json.dumps(body).lower()
            check("path traversal finding present",
                  "traversal" in body_str or "path" in body_str or "security" in body_str,
                  str(body)[:80])

        # ── 10. EXECUTE — BEHAVIORAL LOOP ────────────────────────────────────
        print("\n[9] Execute — Behavioral Loop (6+ same tool, expect anomalous_behavior)")
        loop_findings = []
        last_action = ""
        for i in range(8):
            r = post(c, "/execute",
                     json={"tool": "execute_command",
                           "payload": {"cmd": "ls"},
                           "agent_id": exec_agent_id},
                     headers=exec_headers)
            if r.status_code == 200:
                d = r.json().get("data", {}) or r.json()
                loop_findings = d.get("findings", [])
                last_action = d.get("action", "")
        check("loop execute eventually flags anomalous_behavior OR deny",
              "anomalous_behavior_detected" in loop_findings or last_action in ("deny", "kill"),
              f"findings after 8x={loop_findings} action={last_action}")

        # ── 11. AUDIT ────────────────────────────────────────────────────────
        print("\n[10] Audit")
        time.sleep(1)
        r = post(c, "/audit/logs/search",
                 json={"limit": 20},
                 headers=auth)
        check("POST /audit/logs/search → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json()
            raw = d.get("data", [])
            logs = (raw.get("items") or raw.get("data") or raw) if isinstance(raw, dict) else raw
            check("audit search returns logs", len(logs) > 0, f"count={len(logs)}")

        r = get(c, "/audit/logs/summary", headers=auth)
        check("GET /audit/logs/summary → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json().get("data", {}) or {}
            total = d.get("total_requests", d.get("total_calls", 0))
            blocked = d.get("blocked_requests", d.get("total_denials", 0))
            check("summary total > 0", total > 0, f"total={total} blocked={blocked}")

        # ── 12. AUDIT CHAIN VERIFY ───────────────────────────────────────────
        print("\n[11] Audit Chain Integrity")
        r = get(c, "/audit/logs/verify", headers=auth)
        check("GET /audit/logs/verify → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json().get("data", {}) or r.json()
            violations = d.get("violations", [])
            rows_verified = d.get("rows_verified", 0)
            violation_count = len(violations) if isinstance(violations, list) else int(violations or 0)
            check("chain violations = 0", violation_count == 0,
                  f"violations={violation_count} rows={rows_verified}")

        # ── 13. TRANSPARENCY ROOTS ───────────────────────────────────────────
        print("\n[12] Transparency Log")
        r = get(c, "/transparency/roots", headers=auth)
        check("GET /transparency/roots → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            roots = r.json().get("data", [])
            check("transparency roots list non-empty", len(roots) > 0, f"count={len(roots)}")

        # verify-root is POST on the gateway
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
        r = post(c, "/transparency/verify-root",
                 json={"date": yesterday},
                 headers=auth)
        check(f"POST /transparency/verify-root?date={yesterday} → 200",
              r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json().get("data", {}) or r.json()
            verified = d.get("valid", d.get("verified", False))
            check("transparency root verified", verified is True, str(d)[:80])

        # ── 14. DECISION HISTORY ─────────────────────────────────────────────
        print("\n[13] Decision History")
        r = get(c, "/decision/history?limit=10", headers=auth)
        check("GET /decision/history → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json()
            raw = d.get("data", {}) or {}
            items = raw.get("items", raw.get("data", raw if isinstance(raw, list) else []))
            check("decision history non-empty", len(items) > 0, f"count={len(items)}")

        # ── 15. FLIGHT RECORDER ──────────────────────────────────────────────
        print("\n[14] Flight Recorder")
        r = get(c, "/flight/timelines?limit=5", headers=auth)
        check("GET /flight/timelines → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json().get("data", [])
            items = d if isinstance(d, list) else (d.get("items", d.get("data", [])) if isinstance(d, dict) else [])
            check("flight timelines non-empty", len(items) > 0, f"count={len(items)}")

        # ── 16. USAGE / QUOTA ────────────────────────────────────────────────
        print("\n[15] Usage & Quota")
        r = get(c, "/tenant/quota", headers=auth)
        check("GET /tenant/quota → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json()
            limits = d.get("limits", {})
            check("quota has limits", bool(limits), str(limits)[:60])

        r = get(c, "/usage/summary", headers=auth)
        check("GET /usage/summary → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json().get("data", {}) or {}
            total = d.get("total_units", d.get("record_count", d.get("total_calls", d.get("total_requests", 0))))
            check("usage total > 0", total > 0, f"total={total}")

        # ── 17. IDENTITY GRAPH ───────────────────────────────────────────────
        print("\n[16] Identity Graph")
        r = get(c, "/graph/agents", headers=auth)
        check("GET /graph/agents → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            d = r.json().get("data", {}) or {}
            nodes = d.get("nodes", [])
            check("graph nodes non-empty", len(nodes) > 0, f"count={len(nodes)}")

        # ── 18. AUTONOMY ─────────────────────────────────────────────────────
        print("\n[17] Autonomy / Contracts")
        r = get(c, "/autonomy/contracts", headers=auth)
        check("GET /autonomy/contracts → 200", r.status_code == 200, r.text[:80])

        # ── 19. FORENSICS ────────────────────────────────────────────────────
        print("\n[18] Forensics")
        r = get(c, "/forensics/investigation", headers=auth)
        check("GET /forensics/investigation → 200", r.status_code == 200, r.text[:80])

        # ── 20. PROMETHEUS METRICS ───────────────────────────────────────────
        print("\n[19] Prometheus Metrics")
        r = get(c, "/metrics")
        check("GET /metrics → 200", r.status_code == 200, r.text[:80])
        if r.status_code == 200:
            check("metrics contains acp_", "acp_" in r.text, r.text[:100])

        # ── 21. KILL SWITCH ──────────────────────────────────────────────────
        print("\n[20] Kill Switch")
        r = get(c, "/status")
        if r.status_code == 200:
            ks = r.json().get("kill_switch", {})
            check("kill switch not engaged", ks.get("engaged") is False, str(ks))

    # ── SUMMARY ─────────────────────────────────────────────────────────────
    total = PASS + FAIL
    print(f"\n{'='*60}")
    print(f"RESULT: {PASS}/{total} passed  |  {FAIL} failed")
    print("=" * 60)
    if FAIL > 0:
        print("\nFAILED TESTS:")
        for label, passed, detail in _results:
            if not passed:
                print(f"  ✗ {label}: {detail[:120]}")
    else:
        print("✅ All tests passed")
    return FAIL == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
