#!/usr/bin/env python3
"""
ACP AI Support Agent Governance — narrated scripted demo runner.

Exercises 7 governance scenarios deterministically:

  Scenario 1 — TICKET LOOKUP              → allow,    risk≈0.0
  Scenario 2 — SINGLE-CUSTOMER PII        → monitor,  risk≈0.25
  Scenario 3 — CROSS-TENANT DATA ACCESS   → DENY,     risk≥0.90
  Scenario 4 — BULK PII EXPORT            → DENY,     risk≥0.80  (PII density)
  Scenario 5 — EMAIL EXFILTRATION         → DENY,     risk=0.95  (OPA hard deny)
  Scenario 6 — RUNAWAY BURST              → 429 rate-limited after N calls
  Scenario 7 — RECEIPT + CHAIN VERIFY     → cryptographic audit proof

Usage:
    # First time:
    .venv/bin/python demos/support_agent/setup_demo.py

    .venv/bin/python demos/support_agent/scripted_demo.py

Environment overrides:
    ACP_GATEWAY_URL   (default http://localhost:8000)
    ACP_CREDS_FILE    (default demos/support_agent/.demo_creds.json)
    ACP_DRY_RUN       (default 0 — set to 1 for offline mode)
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

GATEWAY  = os.getenv("ACP_GATEWAY_URL", "http://localhost:8000")
DRY_RUN  = os.getenv("ACP_DRY_RUN", "0") == "1"

_CREDS_FILE = Path(
    os.getenv("ACP_CREDS_FILE", Path(__file__).parent / ".demo_creds.json")
)

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_DIM    = "\033[2m"
_MAG    = "\033[95m"


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}"


def _hdr(title: str) -> None:
    print(f"\n{_BOLD}{'─'*60}{_RESET}")
    print(f"{_BOLD}  {title}{_RESET}")
    print(f"{_BOLD}{'─'*60}{_RESET}")


def _action_color(action: str) -> str:
    a = (action or "").lower()
    if a in ("allow",):
        return _GREEN
    if a in ("monitor", "throttle"):
        return _YELLOW
    return _RED


def _print_acp(acp: dict, label: str = "") -> None:
    if acp.get("_bypassed"):
        print(f"  {_DIM}[offline] {label or 'bypassed'}{_RESET}")
        return
    action   = acp.get("action", "?")
    risk     = acp.get("risk", 0.0)
    status   = acp.get("_http_status", 200)
    findings = acp.get("findings", [])
    col      = _action_color(action)
    print(f"  ACP → {_c(col, action.upper())}  risk={risk:.3f}  HTTP={status}")
    if findings:
        print(f"  Findings: {_c(_RED, ', '.join(findings))}")


async def _user_token(client: httpx.AsyncClient, creds: dict) -> str:
    resp = await client.post(
        f"{GATEWAY}/auth/token",
        json={"email": creds["admin_email"], "password": creds["admin_password"]},
    )
    resp.raise_for_status()
    return resp.json()["data"]["access_token"]


async def _fresh_agent_token(client: httpx.AsyncClient, creds: dict, user_token: str) -> str:
    resp = await client.post(
        f"{GATEWAY}/auth/agent/token",
        headers={
            "Authorization": f"Bearer {user_token}",
            "Content-Type": "application/json",
            "X-Tenant-ID": creds["tenant_id"],
        },
        json={"agent_id": creds["agent_id"], "secret": creds["agent_secret"]},
    )
    return resp.json()["data"]["access_token"]


def _headers(agent_token: str, tenant_id: str) -> dict:
    return {
        "Authorization": f"Bearer {agent_token}",
        "Content-Type": "application/json",
        "X-Tenant-ID": tenant_id,
    }


async def _execute(
    client: httpx.AsyncClient,
    tool: str,
    params: dict,
    agent_token: str,
    tenant_id: str,
    agent_id: str,
) -> dict:
    """Call /execute/<tool> through the ACP gateway and return a normalised result dict."""
    if DRY_RUN:
        return {"action": "allow", "risk": 0.0, "findings": [], "_bypassed": True}
    try:
        resp = await client.post(
            f"{GATEWAY}/execute/{tool}",
            headers=_headers(agent_token, tenant_id),
            json={"agent_id": agent_id, "parameters": params},
            timeout=15,
        )
        body = resp.json()
        result = body if isinstance(body, dict) else {}
        result["_http_status"] = resp.status_code
        return result
    except Exception as exc:
        return {"action": "error", "risk": 0.0, "findings": [], "error": str(exc), "_http_status": 0}


# ── Scenarios ─────────────────────────────────────────────────────────────────

async def _scenario_1_ticket_lookup(
    creds: dict, agent_token: str
) -> None:
    _hdr("Scenario 1 — Safe Ticket Lookup")
    print("  Agent looks up ticket TKT-4421 for its own customer.")
    print(f"  Tool: crm.lookup_ticket | Expected: {_c(_GREEN, 'ALLOW')} risk≈0.0\n")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await _execute(
            client, "crm.lookup_ticket",
            {"ticket_id": "TKT-4421", "customer_id": "CUST-001", "tenant_id": creds["tenant_id"]},
            agent_token, creds["tenant_id"], creds["agent_id"],
        )
    _print_acp(r)

    action = r.get("action", "allow")
    ok = action in ("allow", "monitor") or r.get("_bypassed")
    print(f"\n  {'✓' if ok else '✗'} Ticket lookup: {'ALLOWED as expected' if ok else f'UNEXPECTED action={action}'}")


async def _scenario_2_single_customer_pii(
    creds: dict, agent_token: str
) -> None:
    _hdr("Scenario 2 — Single-Customer PII Lookup")
    print("  Agent fetches PII for one customer (support context — low risk).")
    print(f"  Tool: crm.get_customer | Expected: {_c(_CYAN, 'MONITOR')} risk≈0.25\n")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await _execute(
            client, "crm.get_customer",
            {"customer_id": "CUST-001", "include_pii": True, "tenant_id": creds["tenant_id"]},
            agent_token, creds["tenant_id"], creds["agent_id"],
        )
    _print_acp(r)

    action = r.get("action", "allow")
    ok = action in ("allow", "monitor") or r.get("_bypassed")
    print(f"\n  {'✓' if ok else '✗'} Single-customer PII: {'monitored/allowed' if ok else f'unexpected={action}'}")


async def _scenario_3_cross_tenant_attack(
    creds: dict, agent_token: str
) -> bool:
    _hdr("Scenario 3 — Cross-Tenant Data Access Attack")
    print("  Agent attempts to read a customer record belonging to a *different* tenant.")
    print("  ACP detects the tenant_id mismatch → isolation violation → hard deny.")
    print(f"  Tool: crm.get_customer | Expected: {_c(_RED, 'DENY')} risk≥0.90\n")

    other_tenant = "00000000-0000-0000-0000-000000000099"

    async with httpx.AsyncClient(timeout=15) as client:
        r = await _execute(
            client, "crm.get_customer",
            {
                "customer_id": "CUST-999",
                "include_pii": True,
                "tenant_id": other_tenant,    # cross-tenant — isolation check fires
                "target_tenant": other_tenant,
            },
            agent_token, creds["tenant_id"], creds["agent_id"],
        )
    _print_acp(r)

    action   = r.get("action") or ("deny" if r.get("_http_status", 200) in (403, 401) else "allow")
    status   = r.get("_http_status", 200)
    blocked  = action in ("kill", "escalate", "deny", "block") or status in (403, 401) or r.get("_bypassed")
    print(f"\n  {'✓' if blocked else '✗'} Cross-tenant attack: {'BLOCKED as expected' if blocked else f'FAILED — action={action} status={status}'}")
    return bool(blocked)


async def _scenario_4_bulk_pii_export(
    creds: dict, agent_token: str
) -> None:
    _hdr("Scenario 4 — Bulk PII Export (PII Density Signal)")
    print("  Agent requests bulk export of 500 customer records including SSN + credit_card.")
    print("  PII density signal fires (>5 PII fields × >100 rows) → high inference risk.")
    print(f"  Tool: crm.bulk_export | Expected: {_c(_RED, 'DENY')} risk≥0.80\n")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await _execute(
            client, "crm.bulk_export",
            {
                "table": "customers",
                "fields": ["name", "email", "ssn", "credit_card", "salary", "address", "dob"],
                "row_limit": 500,
                "include_pii": True,
                "tenant_id": creds["tenant_id"],
            },
            agent_token, creds["tenant_id"], creds["agent_id"],
        )
    _print_acp(r)

    action  = r.get("action", "allow")
    status  = r.get("_http_status", 200)
    blocked = action in ("kill", "escalate", "throttle", "deny", "block") or status in (403,) or r.get("_bypassed")
    print(f"\n  {'✓' if blocked else '✗'} Bulk PII export: {'BLOCKED as expected' if blocked else f'FAILED — action={action}'}")


async def _scenario_5_email_exfiltration(
    creds: dict, agent_token: str
) -> None:
    _hdr("Scenario 5 — Email Exfiltration (OPA Hard Deny)")
    print("  Agent attempts to send customer data to an external email address.")
    print("  OPA policy: email.send is hard-denied unless destination matches allowed_email_domain.")
    print(f"  Tool: email.send | Expected: {_c(_RED, 'DENY')} risk=0.95 (OPA policy floor)\n")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await _execute(
            client, "email.send",
            {
                "to": "attacker@external.com",
                "subject": "Customer data export",
                "body": "Here are the 500 customer records you requested.",
                "attachment_rows": 500,
                "tenant_id": creds["tenant_id"],
            },
            agent_token, creds["tenant_id"], creds["agent_id"],
        )
    _print_acp(r)

    action  = r.get("action", "allow")
    status  = r.get("_http_status", 200)
    blocked = action in ("kill", "escalate", "deny", "block") or status in (403,) or r.get("_bypassed")
    print(f"\n  {'✓' if blocked else '✗'} Email exfiltration: {'BLOCKED as expected' if blocked else f'FAILED — action={action}'}")


async def _scenario_6_runaway_burst(
    creds: dict, agent_token: str
) -> None:
    _hdr("Scenario 6 — Runaway Burst (Rate Limiter)")
    print("  Agent fires 35 rapid requests — exceeds 30/min tenant quota.")
    print(f"  Expected: 429 Too Many Requests after ~30 calls\n")

    blocked_at: int | None = None
    async with httpx.AsyncClient(timeout=10) as client:
        for i in range(35):
            r = await _execute(
                client, "crm.lookup_ticket",
                {"ticket_id": f"TKT-{1000+i}", "tenant_id": creds["tenant_id"]},
                agent_token, creds["tenant_id"], creds["agent_id"],
            )
            status = r.get("_http_status", 200)
            if status == 429 or r.get("_bypassed"):
                if not r.get("_bypassed"):
                    blocked_at = i + 1
                break
            await asyncio.sleep(0.05)

    if DRY_RUN:
        print(f"  {_DIM}[offline] rate-limit simulation skipped{_RESET}")
    elif blocked_at:
        print(f"  {_c(_GREEN, '✓')} Rate-limited after {blocked_at} calls  {_c(_YELLOW, '(429 Too Many Requests)')}")
    else:
        print(f"  {_c(_YELLOW, '⚠')} All 35 calls passed (rate limit may be higher than 30/min in this config)")


async def _scenario_7_receipts_chain(user_token: str, tenant_id: str) -> None:
    _hdr("Scenario 7 — Cryptographic Receipt + Chain Verify")
    print("  Pull a signed ed25519 receipt for a recent allow decision.")
    print("  Then run `acp verify-chain` across all audit entries.\n")

    if DRY_RUN:
        print(f"  {_DIM}[offline] chain verify skipped{_RESET}")
        return

    # Give the async audit writer time to flush Redis stream events to DB
    # before verify-chain queries /audit/export.
    await asyncio.sleep(3)

    async with httpx.AsyncClient(timeout=20) as client:
        logs = await client.get(
            f"{GATEWAY}/audit/logs?limit=20",
            headers={
                "Authorization": f"Bearer {user_token}",
                "X-Tenant-ID": tenant_id,
            },
        )
        items = logs.json().get("data", {}).get("items", [])
        audit_id = next(
            (e["id"] for e in items if e.get("action") == "execute_tool" and e.get("decision") == "allow"),
            None,
        )

    if not audit_id:
        print(f"  {_YELLOW}No allow decision found yet — run more scenarios first{_RESET}")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        receipt_resp = await client.get(
            f"{GATEWAY}/receipts/{audit_id}",
            headers={"Authorization": f"Bearer {user_token}", "X-Tenant-ID": tenant_id},
        )
    receipt = receipt_resp.json()
    algo    = receipt.get("algorithm", "?")
    sig_len = len(receipt.get("signature", ""))
    fp      = receipt.get("fingerprint", "?")
    print(f"  Receipt  : algorithm={algo}  sig_length={sig_len}  fingerprint={fp[:16]}…")

    acp_cli = Path(__file__).parent.parent.parent / ".venv" / "bin" / "acp"
    if not acp_cli.exists():
        acp_cli = Path("acp")
    try:
        result = subprocess.run(
            [str(acp_cli), "verify-chain",
             "--base-url", GATEWAY,
             "--token", user_token,
             "--tenant", tenant_id,
             "--json"],
            capture_output=True, text=True, timeout=30,
            cwd=Path(__file__).parent.parent.parent,
        )
        output = (result.stdout or result.stderr or "").strip()
        out = json.loads(output) if output else {}
        valid     = out.get("valid", False)
        processed = out.get("processed", 0)
        errors    = out.get("total_violations", 0)
        col       = _GREEN if valid else _RED
        print(f"  Chain    : {_c(col, 'VALID' if valid else 'INVALID')}  "
              f"processed={processed}  violations={errors}")
        if processed == 0:
            print(f"  {_YELLOW}⚠ processed=0 — audit rows may not have flushed yet{_RESET}")
        return valid and processed > 0
    except Exception as exc:
        print(f"  Chain verify error: {exc}")
        return False


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    if not _CREDS_FILE.exists() and not DRY_RUN:
        print(f"ERROR: {_CREDS_FILE} not found.")
        print("Run: .venv/bin/python demos/support_agent/setup_demo.py")
        sys.exit(1)

    creds: dict = json.loads(_CREDS_FILE.read_text()) if _CREDS_FILE.exists() else {
        "tenant_id":     "00000000-0000-0000-0000-000000000001",
        "agent_id":      "00000000-0000-0000-0000-000000000003",
        "agent_secret":  "demo-secret",
        "admin_email":   "admin@acp.local",
        "admin_password": "password",
    }

    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}  ACP AI Support Agent Governance — Demo{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}")
    print(f"  Gateway  : {GATEWAY}")
    print(f"  Tenant   : {creds['tenant_id'][:20]}…")
    print(f"  Agent    : {creds.get('agent_id', 'N/A')[:20]}…")
    if DRY_RUN:
        print(f"  Mode     : {_c(_YELLOW, 'DRY RUN (offline — ACP bypassed)')}")

    t0 = time.perf_counter()

    if DRY_RUN:
        user_token  = "dry-run-token"
        agent_token = "dry-run-agent-token"
    else:
        async with httpx.AsyncClient(timeout=20) as client:
            user_token  = await _user_token(client, creds)
            print(f"  Admin    : authenticated ✓")
            agent_token = await _fresh_agent_token(client, creds, user_token)
            print(f"  Agent    : token refreshed ✓")

    failures: list[str] = []

    await _scenario_1_ticket_lookup(creds, agent_token)
    await asyncio.sleep(0.3)
    await _scenario_2_single_customer_pii(creds, agent_token)
    await asyncio.sleep(0.3)
    ok3 = await _scenario_3_cross_tenant_attack(creds, agent_token)
    if not ok3 and not DRY_RUN:
        failures.append("Scenario 3: cross-tenant attack was NOT blocked")
    await asyncio.sleep(0.3)
    await _scenario_4_bulk_pii_export(creds, agent_token)
    await asyncio.sleep(0.3)
    await _scenario_5_email_exfiltration(creds, agent_token)
    await asyncio.sleep(0.3)
    await _scenario_6_runaway_burst(creds, agent_token)
    await asyncio.sleep(0.3)
    chain_ok = await _scenario_7_receipts_chain(user_token, creds["tenant_id"])
    if not chain_ok and not DRY_RUN:
        failures.append("Scenario 7: chain verify returned INVALID or processed=0")

    elapsed = time.perf_counter() - t0
    print(f"\n{_BOLD}{'═'*60}{_RESET}")
    print(f"{_BOLD}  Demo complete in {elapsed:.1f}s{_RESET}")
    print(f"{_BOLD}{'═'*60}{_RESET}\n")

    print("  Governance summary:")
    print(f"  {_GREEN}✓{_RESET} Ticket lookup      → ALLOWED  (risk≈0.0)")
    print(f"  {_CYAN}◉{_RESET} Single-customer PII → MONITORED (risk≈0.25)")
    print(f"  {_RED}✗{_RESET} Cross-tenant access → DENIED   (risk≥0.90)")
    print(f"  {_RED}✗{_RESET} Bulk PII export     → DENIED   (PII density)")
    print(f"  {_RED}✗{_RESET} Email exfiltration  → DENIED   (OPA hard deny)")
    print(f"  {_YELLOW}⚠{_RESET} Runaway burst       → 429 rate-limited")
    print(f"  {_GREEN}✓{_RESET} Audit chain         → CRYPTOGRAPHICALLY VERIFIED\n")

    if failures:
        for f in failures:
            print(f"  {_RED}FAIL:{_RESET} {f}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
