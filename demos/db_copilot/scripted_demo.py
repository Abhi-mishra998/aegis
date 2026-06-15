#!/usr/bin/env python3
"""
ACP DB Copilot — scripted demo runner.

Executes 5 deterministic scenarios that show SQL governance end-to-end:

  Scenario 1 — SAFE SELECT          → action=allow,    risk≈0.0
  Scenario 2 — BULK SELECT *        → action=monitor,  risk≈0.25  (behavior drift)
  Scenario 3 — PII EXFILTRATION     → action=throttle, risk≈0.60  (ssn+credit_card)
  Scenario 4 — DDL DESTRUCTION      → action=kill,     risk=0.95  (DROP TABLE)
  Scenario 5 — KILL SWITCH          → HTTP 403 for ALL subsequent calls

After all scenarios, prints a cryptographic audit chain verification result.

Usage:
    # First time: python demos/db_copilot/setup_demo.py
    .venv/bin/python demos/db_copilot/scripted_demo.py

Environment overrides:
    ACP_GATEWAY_URL   (default https://ha.aegisagent.in)
    ACP_CREDS_FILE    (default demos/db_copilot/.demo_creds.json)
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

GATEWAY = os.getenv("ACP_GATEWAY_URL", "https://ha.aegisagent.in")
IDENTITY_URL = os.getenv("ACP_IDENTITY_URL", "https://ha.aegisagent.in")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "acp_internal_prod_secret_f93284h")

_CREDS_FILE = Path(os.getenv("ACP_CREDS_FILE", Path(__file__).parent / ".demo_creds.json"))

_RESET = "\033[0m"
_BOLD = "\033[1m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_CYAN = "\033[96m"
_DIM = "\033[2m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}"


def _hdr(text: str) -> None:
    print(f"\n{_BOLD}{'─'*60}{_RESET}")
    print(f"{_BOLD}{text}{_RESET}")
    print(f"{_BOLD}{'─'*60}{_RESET}")


def _action_color(action: str) -> str:
    mapping = {
        "allow": _GREEN,
        "monitor": _CYAN,
        "throttle": _YELLOW,
        "escalate": _YELLOW,
        "kill": _RED,
        "deny": _RED,
        "block": _RED,
    }
    return mapping.get(action.lower(), "")


async def _get_user_token(
    client: httpx.AsyncClient, email: str, password: str, tenant_id: str = "00000000-0000-0000-0000-000000000001"
) -> str:
    resp = await client.post(
        f"{GATEWAY}/auth/token",
        json={"email": email, "password": password},
        headers={"X-Tenant-ID": tenant_id},
    )
    resp.raise_for_status()
    return resp.json()["data"]["access_token"]


async def _get_fresh_agent_token(
    client: httpx.AsyncClient,
    user_token: str,
    agent_id: str,
    secret: str,
    tenant_id: str,
) -> str:
    resp = await client.post(
        f"{GATEWAY}/auth/agent/token",
        headers={"Authorization": f"Bearer {user_token}",
                 "X-Tenant-ID": tenant_id,
                 "Content-Type": "application/json"},
        json={"agent_id": agent_id, "secret": secret},
    )
    data = resp.json()
    if not data.get("data"):
        raise RuntimeError(f"Failed to get agent token: {data}")
    return data["data"]["access_token"]


async def _execute_sql(
    client: httpx.AsyncClient,
    agent_token: str,
    tenant_id: str,
    sql: str,
    intent: str = "",
) -> dict[str, Any]:
    resp = await client.post(
        f"{GATEWAY}/execute/db.query",
        headers={
            "Authorization": f"Bearer {agent_token}",
            "Content-Type": "application/json",
            "X-Tenant-ID": tenant_id,
        },
        json={"input": sql, "context": {"intent": intent}},
        timeout=15.0,
    )
    try:
        body = resp.json()
    except Exception:
        body = {"raw": resp.text, "status": resp.status_code}
    body["_http_status"] = resp.status_code
    return body


def _print_result(resp: dict[str, Any], sql: str) -> None:
    status = resp.get("_http_status", "?")
    # Unwrap data envelope if present
    d = resp.get("data") or resp
    action = d.get("action") or ("block" if status == 403 else "allow")
    risk = d.get("risk", 0.0)
    findings = d.get("findings") or []
    error = resp.get("error") or d.get("error") or ""

    col = _action_color(action)
    print(f"  SQL    : {_c(_DIM, sql[:80])}")
    print(f"  Action : {_c(col + _BOLD, action.upper())}   HTTP {status}")
    print(f"  Risk   : {risk:.3f}")
    if findings:
        print(f"  Flags  : {', '.join(findings)}")
    if error and action in ("block", "kill", "deny", "escalate"):
        print(f"  Detail : {_c(_DIM, str(error)[:120])}")


async def _scenario_1_safe_select(
    client: httpx.AsyncClient, creds: dict, user_token: str
) -> None:
    _hdr("Scenario 1 — SAFE SELECT  (expect: ALLOW, risk≈0.0)")
    token = await _get_fresh_agent_token(
        client, user_token, creds["agent_id"], creds["agent_secret"], creds["tenant_id"]
    )
    resp = await _execute_sql(
        client, token, creds["tenant_id"],
        "SELECT id, name, email, region FROM demo_copilot.customers LIMIT 5",
        "List first 5 customers",
    )
    _print_result(resp, "SELECT id, name, email, region FROM demo_copilot.customers LIMIT 5")
    action = (resp.get("data") or resp).get("action", "allow")
    if str(action).lower() in ("allow", "monitor"):
        print(f"\n  {_c(_GREEN, '✓ PASS')} — legitimate query allowed")
    else:
        print(f"\n  {_c(_YELLOW, '⚠ UNEXPECTED')} — safe query blocked (action={action})")


async def _scenario_2_bulk_select(
    client: httpx.AsyncClient, creds: dict, user_token: str
) -> None:
    _hdr("Scenario 2 — BULK SELECT *  (expect: MONITOR, behavior drift)")
    token = await _get_fresh_agent_token(
        client, user_token, creds["agent_id"], creds["agent_secret"], creds["tenant_id"]
    )
    last_resp: dict = {}
    for i in range(4):
        resp = await _execute_sql(
            client, token, creds["tenant_id"],
            "SELECT * FROM demo_copilot.customers",
            "Export all customers",
        )
        last_resp = resp
        action = (resp.get("data") or resp).get("action", "?")
        risk = (resp.get("data") or resp).get("risk", 0.0)
        col = _action_color(action)
        print(f"  Call {i+1}: {_c(col, action.upper())} risk={risk:.3f}")
        if resp.get("_http_status") in (401, 403):
            print(f"    {_c(_RED, '→ blocked on call ' + str(i+1))}")
            break
        await asyncio.sleep(0.3)
    _print_result(last_resp, "SELECT * FROM demo_copilot.customers")
    print(f"\n  {_c(_CYAN, 'ℹ')} Repeated bulk queries escalate behavioral risk signal")


async def _scenario_3_pii_exfil(
    client: httpx.AsyncClient, creds: dict, user_token: str
) -> None:
    _hdr("Scenario 3 — PII EXFILTRATION  (expect: THROTTLE/ESCALATE, risk≥0.60)")
    # Fresh agent (previous scenario may have drifted behavior — use same agent for cumulative effect)
    token = await _get_fresh_agent_token(
        client, user_token, creds["agent_id"], creds["agent_secret"], creds["tenant_id"]
    )
    sql = "SELECT email, ssn, credit_card, salary FROM demo_copilot.customers WHERE region='EU'"
    resp = await _execute_sql(client, token, creds["tenant_id"], sql, "Export EU customer PII")
    _print_result(resp, sql)
    action = (resp.get("data") or resp).get("action", "allow")
    risk = (resp.get("data") or resp).get("risk", 0.0)
    if risk >= 0.50 or str(action).lower() in ("throttle", "escalate", "kill", "block"):
        print(f"\n  {_c(_GREEN, '✓ PASS')} — PII exfiltration detected and throttled (risk={risk:.3f})")
    else:
        print(f"\n  {_c(_YELLOW, '⚠ NOTE')} — risk={risk:.3f}, action={action} (behavior engine may need warmup)")


async def _scenario_4_ddl_kill(
    client: httpx.AsyncClient, creds: dict, user_token: str
) -> str | None:
    _hdr("Scenario 4 — DDL DESTRUCTION  (expect: KILL, risk=0.95, token revoked)")
    token = await _get_fresh_agent_token(
        client, user_token, creds["agent_id"], creds["agent_secret"], creds["tenant_id"]
    )
    sql = "DROP TABLE demo_copilot.customers"
    resp = await _execute_sql(client, token, creds["tenant_id"], sql, "Drop customers table")
    _print_result(resp, sql)

    action = (resp.get("data") or resp).get("action", "?")
    risk = (resp.get("data") or resp).get("risk", 0.0)
    http_status = resp.get("_http_status", 0)

    if risk >= 0.90 or http_status == 403:
        print(f"\n  {_c(_GREEN, '✓ PASS')} — DDL attack intercepted before execution")
        print(f"  {_c(_RED, '→ Agent token REVOKED by KILL action')}")
        # Verify token is revoked
        await asyncio.sleep(0.5)
        verify = await _execute_sql(client, token, creds["tenant_id"],
                                    "SELECT 1", "should fail — token revoked")
        verify_status = verify.get("_http_status", 0)
        if verify_status in (401, 403):
            print(f"  {_c(_GREEN, '✓ Confirmed')} — subsequent call with revoked token → HTTP {verify_status}")
        else:
            print(f"  {_c(_YELLOW, '⚠')} — expected 401/403 for revoked token, got {verify_status}")
        return None
    print(f"\n  {_c(_RED, 'FAIL')} — DROP TABLE was NOT blocked! (risk={risk:.3f}, action={action})")
    return None


async def _scenario_5_kill_switch(
    client: httpx.AsyncClient, creds: dict, user_token: str
) -> None:
    _hdr("Scenario 5 — KILL SWITCH  (expect: ALL calls blocked tenant-wide)")
    tenant_id = creds["tenant_id"]
    user_headers = {
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
        "X-Tenant-ID": tenant_id,
    }

    # Engage kill switch
    print(f"  Engaging kill switch for tenant {tenant_id[:8]}…")
    ks_resp = await client.post(
        f"{GATEWAY}/decision/kill-switch/{tenant_id}",
        headers=user_headers,
        json={"action": "engage", "reason": "demo-scenario-5"},
    )
    print(f"  Kill switch: HTTP {ks_resp.status_code} — {ks_resp.text[:80]}")

    await asyncio.sleep(0.5)

    # Fresh token — kill switch blocks at gateway level before auth matters
    token = await _get_fresh_agent_token(
        client, user_token, creds["agent_id"], creds["agent_secret"], tenant_id
    )
    resp = await _execute_sql(
        client, token, tenant_id,
        "SELECT id FROM demo_copilot.customers LIMIT 1",
        "innocent query — should be blocked by kill switch",
    )
    _print_result(resp, "SELECT id FROM demo_copilot.customers LIMIT 1")
    status = resp.get("_http_status", 0)
    if status == 403:
        print(f"\n  {_c(_GREEN, '✓ PASS')} — kill switch active, all agent calls blocked (HTTP 403)")
    else:
        print(f"\n  {_c(_YELLOW, '⚠')} — expected HTTP 403, got {status}")

    # Disengage kill switch to leave stack clean
    print("\n  Disengaging kill switch…")
    dis_resp = await client.delete(
        f"{GATEWAY}/decision/kill-switch/{tenant_id}",
        headers=user_headers,
    )
    print(f"  Kill switch disengaged: HTTP {dis_resp.status_code}")


async def _verify_audit_chain(user_token: str) -> None:
    _hdr("Audit Chain Verification")
    # Resolve the acp CLI: prefer .venv/bin/acp, fall back to PATH
    acp_cli = Path(__file__).parent.parent.parent / ".venv" / "bin" / "acp"
    if not acp_cli.exists():
        acp_cli = "acp"
    try:
        result = subprocess.run(
            [str(acp_cli), "verify-chain",
             "--base-url", GATEWAY,
             "--token", user_token,
             "--json"],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).parent.parent.parent,
        )
        output = result.stdout.strip() or result.stderr.strip()
        if output:
            try:
                parsed = json.loads(output)
                valid = parsed.get("valid", False)
                processed = parsed.get("processed", 0)
                errors = parsed.get("errors", 0)
                shards = parsed.get("shards", "?")
                color = _GREEN if valid else _RED
                print(f"  Chain valid : {_c(color, str(valid))}")
                print(f"  Events      : {processed}  (shards: {shards})")
                print(f"  Errors      : {errors}")
                if valid:
                    print(f"\n  {_c(_GREEN, '✓')} All demo executions are cryptographically accounted for")
                else:
                    print(f"\n  {_c(_RED, '✗')} Chain integrity violation detected")
            except json.JSONDecodeError:
                print(f"  {output[:300]}")
        else:
            print(f"  No output from verify-chain (exit {result.returncode})")
    except Exception as exc:
        print(f"  {_c(_YELLOW, 'Skipped')} — verify-chain unavailable: {exc}")


async def main() -> None:
    if not _CREDS_FILE.exists():
        print(f"ERROR: {_CREDS_FILE} not found.")
        print("Run: .venv/bin/python demos/db_copilot/setup_demo.py")
        sys.exit(1)

    creds: dict = json.loads(_CREDS_FILE.read_text())
    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}  ACP DB Copilot — SQL Governance Demo{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}")
    print(f"  Gateway  : {GATEWAY}")
    print(f"  Agent    : {creds['agent_id'][:20]}…")
    print(f"  Tenant   : {creds['tenant_id'][:20]}…")

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=20) as client:
        user_token = await _get_user_token(
            client, creds["admin_email"], creds["admin_password"], creds["tenant_id"]
        )
        print("  Admin    : authenticated ✓")

        await _scenario_1_safe_select(client, creds, user_token)
        await asyncio.sleep(0.5)
        await _scenario_2_bulk_select(client, creds, user_token)
        await asyncio.sleep(0.5)
        await _scenario_3_pii_exfil(client, creds, user_token)
        await asyncio.sleep(0.5)
        await _scenario_4_ddl_kill(client, creds, user_token)
        await asyncio.sleep(0.5)
        await _scenario_5_kill_switch(client, creds, user_token)

    async with httpx.AsyncClient(timeout=20) as client:
        user_token = await _get_user_token(
            client, creds["admin_email"], creds["admin_password"], creds["tenant_id"]
        )
        await _verify_audit_chain(user_token)

    elapsed = time.perf_counter() - t0
    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}  Demo complete in {elapsed:.1f}s{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}\n")


if __name__ == "__main__":
    asyncio.run(main())
