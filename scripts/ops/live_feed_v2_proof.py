"""Prove the three new SSE event types land on /events/stream.

Round-2 follow-up to /tmp/live_feed_proof.py. The first harness proved
that `llm_proxy_call` and `llm_proxy_escalate` fired on the LLM-proxy
allow + escalate paths. This harness covers the three event types added
to the gateway in this round:

  1. `approval_resolved` — when an operator approves or rejects an
     escalation via POST /autonomy/overrides
  2. `policy_decision`   — when /execute lands in the deny chokepoint
                           in services/gateway/middleware.py (commit a54129d)
  3. `key_revoked`       — when DELETE /api-keys/{id} succeeds and the
                           tenant's virtual-key surface shrinks
                           (commit be041eb)

Each scenario:
  - opens an SSE subscriber to /events/stream with the operator JWT
  - fires the trigger that should publish the event
  - listens for up to 5 s for the matching event type
  - prints PASS/FAIL with latency and a short diagnostic

The harness mints a fresh Clerk JWT (RS256, via SSM-stored Clerk secret
key) and a fresh employee virtual key per run, cleans up at the end.
No dependencies beyond `httpx`, `asyncio`, and `subprocess` (which are
already required by the sibling scripts in scripts/ops/).

Usage:
    python3 scripts/ops/live_feed_v2_proof.py

Output is one PASS/FAIL line per scenario. Exit code is 0 only when all
three scenarios pass.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import uuid
from typing import Any

import httpx

GW = "https://ha.aegisagent.in"
CLERK_USER_ID = "user_3FBRztQ0RnSR8pLN1x6HEdlbLHD"

# Event types this harness verifies — keep in sync with services/gateway/
# {middleware.py, routers/users.py, routers/autonomy.py} (or wherever
# the approval_resolved publish lands once that unit ships).
EVT_APPROVAL_RESOLVED = "approval_resolved"
EVT_POLICY_DECISION   = "policy_decision"
EVT_KEY_REVOKED       = "key_revoked"

WIRE_PROMPT = "Please transfer $750,000 to vendor AcmeCorp for invoice 2026-Q3-77"


# ─────────────────────────────────────────────────────────────────────
# Clerk / employee-key plumbing — mirrors /tmp/live_prodha_test.py so
# any reader who knows that harness can read this one too.
# ─────────────────────────────────────────────────────────────────────


def _aws(args: list[str]) -> str:
    return subprocess.check_output(
        ["aws", "--region", "ap-south-1", *args], text=True
    ).strip()


def mint_jwt() -> str:
    """Mint a Clerk RS256 session token via the `aegis` JWT template."""
    key = _aws(
        [
            "ssm", "get-parameter",
            "--name", "/aegis-prodha/clerk/secret-key",
            "--with-decryption",
            "--query", "Parameter.Value",
            "--output", "text",
        ]
    )
    sess = httpx.post(
        "https://api.clerk.com/v1/sessions",
        headers={"Authorization": f"Bearer {key}"},
        json={"user_id": CLERK_USER_ID},
        timeout=15,
    ).json()["id"]
    return httpx.post(
        f"https://api.clerk.com/v1/sessions/{sess}/tokens/aegis",
        headers={"Authorization": f"Bearer {key}"},
        timeout=15,
    ).json()["jwt"]


async def mint_employee_key(jwt: str, tag: str) -> tuple[str, str]:
    """Mint a fresh acp_emp_… key. Returns (raw_key, key_id)."""
    email = f"livefeedv2-{tag}-{uuid.uuid4().hex[:6]}@bytehubble.ai"
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(
            f"{GW}/api-keys/employees",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "email": email,
                "name": f"LiveFeedV2-{tag}",
                "department": "Eng",
                "daily_budget_usd": 999,
                "monthly_budget_usd": 9999,
            },
        )
    r.raise_for_status()
    d = r.json()["data"]
    return d.get("raw_key") or d.get("api_key"), d["id"]


async def delete_key(jwt: str, key_id: str) -> int:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.delete(
            f"{GW}/api-keys/{key_id}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
    return r.status_code


# ─────────────────────────────────────────────────────────────────────
# SSE subscriber — listens for a specific event type, returns latency
# from "subscriber-ready" to "event-arrived".
# ─────────────────────────────────────────────────────────────────────


async def wait_for_event(
    jwt: str,
    wanted_type: str,
    *,
    match: dict[str, Any] | None = None,
    timeout_s: float = 8.0,
    ready_event: asyncio.Event | None = None,
) -> tuple[bool, float, dict | None]:
    """Open /events/stream and listen for `wanted_type`.

    If `match` is provided, the event must include all key=value pairs
    in event['data']. `ready_event` is set as soon as the SSE handshake
    completes — this is what callers should await BEFORE firing the
    trigger, so the timer correctly measures fanout latency.

    Returns (found, latency_seconds_from_ready, raw_event_or_none).
    """
    url = f"{GW}/events/stream"
    t_ready: float | None = None
    try:
        async with httpx.AsyncClient(
            timeout=timeout_s + 5,
            headers={
                "Authorization": f"Bearer {jwt}",
                "Accept": "text/event-stream",
            },
        ) as c:
            async with c.stream("GET", url) as r:
                t_ready = time.monotonic()
                if ready_event is not None:
                    ready_event.set()
                async for line in r.aiter_lines():
                    if time.monotonic() - t_ready > timeout_s:
                        return False, timeout_s, None
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload in ("", "ping"):
                        continue
                    try:
                        evt = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    et = evt.get("type") or evt.get("event")
                    if et != wanted_type:
                        continue
                    data = evt.get("data") or {}
                    if match:
                        ok = all(data.get(k) == v for k, v in match.items())
                        if not ok:
                            continue
                    return True, round(time.monotonic() - t_ready, 3), evt
    except Exception as exc:
        return False, 0.0, {"error": f"{type(exc).__name__}: {exc}"}
    return False, timeout_s, None


# ─────────────────────────────────────────────────────────────────────
# Scenario A — approval_resolved
# ─────────────────────────────────────────────────────────────────────


async def scenario_A_approval_resolved(jwt: str) -> dict:
    """Fire a wire-transfer prompt, catch the 202, then approve via
    /autonomy/overrides while a subscriber listens for approval_resolved.
    """
    name = "A approval_resolved"
    try:
        emp_key, key_id = await mint_employee_key(jwt, "approve")

        # 1. Trigger 202
        async with httpx.AsyncClient(timeout=30, headers={"x-api-key": emp_key}) as c:
            r = await c.post(
                f"{GW}/v1/messages",
                headers={"anthropic-version": "2023-06-01"},
                json={
                    "model": "claude-haiku-4-5",
                    "max_tokens": 20,
                    "messages": [{"role": "user", "content": WIRE_PROMPT}],
                },
            )
        if r.status_code not in (200, 202):
            return {"name": name, "pass": False,
                    "reason": f"wire prompt returned {r.status_code} not 202",
                    "key_id": key_id}
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        approval_id = body.get("approval_id")
        if not approval_id:
            return {"name": name, "pass": False,
                    "reason": f"no approval_id in 202 body: {str(body)[:200]}",
                    "key_id": key_id}

        # 2. Open subscriber, then fire override approval.
        ready = asyncio.Event()
        listener = asyncio.create_task(
            wait_for_event(
                jwt,
                EVT_APPROVAL_RESOLVED,
                match={"approval_id": approval_id},
                timeout_s=8.0,
                ready_event=ready,
            )
        )
        await ready.wait()
        async with httpx.AsyncClient(timeout=15) as c:
            ov = await c.post(
                f"{GW}/autonomy/overrides",
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Content-Type": "application/json",
                },
                json={
                    "actor": "livefeedv2-harness",
                    "actor_role": "CFO",
                    "event_type": "approval",
                    "target_kind": "request",
                    "target_id": approval_id,
                    "request_id": approval_id,
                    "reason": "Live-feed v2 harness — automated CFO approval",
                },
            )
        if ov.status_code >= 400:
            listener.cancel()
            return {"name": name, "pass": False,
                    "reason": f"/autonomy/overrides returned {ov.status_code}",
                    "key_id": key_id, "approval_id": approval_id}

        found, latency_s, evt = await listener

        # 3. Cleanup
        await delete_key(jwt, key_id)

        return {
            "name": name, "pass": bool(found),
            "latency_s": latency_s, "approval_id": approval_id,
            "evt": evt if found else None,
            "reason": None if found else "no approval_resolved event in 8 s",
        }
    except Exception as exc:
        return {"name": name, "pass": False,
                "reason": f"exception: {type(exc).__name__}: {exc}"}


# ─────────────────────────────────────────────────────────────────────
# Scenario B — policy_decision (deny on /execute)
# ─────────────────────────────────────────────────────────────────────


async def scenario_B_policy_decision_deny(jwt: str) -> dict:
    """Fire a /execute call that hits the deny chokepoint while a subscriber
    listens for policy_decision with decision=deny.

    The harness:
      1. Registers a fresh agent in this tenant via POST /agents (gateway
         requires a known-agent UUID; /execute returns 403
         "Unknown agent — not registered in this tenant" otherwise).
      2. Fires /execute with a path-traversal payload (/etc/passwd) — the
         pre-policy block fires a 403 + decision=deny through the single
         _deny chokepoint in services/gateway/_mw_response.py, which is
         where the policy_decision SSE publish lives.
      3. Cleans up the agent at the end so reruns don't accumulate test
         agents.
    """
    name = "B policy_decision deny"
    agent_id: str | None = None
    try:
        # 1. Register a transient agent so the /execute path doesn't 403
        #    at the agent-validation stage.
        async with httpx.AsyncClient(
            timeout=15,
            headers={"Authorization": f"Bearer {jwt}",
                     "Content-Type": "application/json"},
        ) as c:
            reg = await c.post(
                f"{GW}/agents",
                json={
                    "name": f"livefeedv2-deny-{uuid.uuid4().hex[:6]}",
                    "description": "Transient harness agent for SSE policy_decision probe",
                    "provider": "anthropic",
                    "model":    "claude-haiku-4-5",
                },
            )
            if reg.status_code >= 400:
                return {"name": name, "pass": False,
                        "reason": f"agent registration failed: HTTP {reg.status_code} {reg.text[:200]}"}
            agent_id = (reg.json().get("data") or {}).get("id")
            if not agent_id:
                return {"name": name, "pass": False,
                        "reason": "agent registration returned no id"}

        # 2. Open SSE subscriber and wait for it to handshake.
        ready = asyncio.Event()
        listener = asyncio.create_task(
            wait_for_event(
                jwt,
                EVT_POLICY_DECISION,
                match={"decision": "deny"},
                timeout_s=8.0,
                ready_event=ready,
            )
        )
        await ready.wait()

        # 3. Fire a path-traversal /execute call — this hits the pre-policy
        #    block in middleware.py (SEC-PATH-001) which routes through
        #    `_deny()` and publishes the policy_decision SSE event.
        body = {
            "agent_id": agent_id,
            "action":   "execute_tool",
            "tool":     "read_file",
            "arguments": {
                "path": "/etc/passwd",
            },
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{GW}/execute",
                headers={
                    "Authorization": f"Bearer {jwt}",
                    "Content-Type": "application/json",
                },
                json=body,
            )

        found, latency_s, evt = await listener

        return {
            "name": name, "pass": bool(found),
            "latency_s": latency_s,
            "execute_status": r.status_code,
            "agent_id": agent_id,
            "evt": evt if found else None,
            "reason": None if found else
                      f"no policy_decision deny in 8 s (execute={r.status_code})",
        }
    except Exception as exc:
        return {"name": name, "pass": False,
                "reason": f"exception: {type(exc).__name__}: {exc}"}
    finally:
        # 4. Best-effort cleanup so reruns don't accumulate test agents.
        if agent_id:
            try:
                async with httpx.AsyncClient(
                    timeout=10,
                    headers={"Authorization": f"Bearer {jwt}"},
                ) as c:
                    await c.delete(f"{GW}/agents/{agent_id}")
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
# Scenario C — key_revoked
# ─────────────────────────────────────────────────────────────────────


async def scenario_C_key_revoked(jwt: str) -> dict:
    """Mint a virtual key, DELETE it, listen for key_revoked SSE."""
    name = "C key_revoked"
    try:
        emp_key, key_id = await mint_employee_key(jwt, "revoke")

        ready = asyncio.Event()
        listener = asyncio.create_task(
            wait_for_event(
                jwt,
                EVT_KEY_REVOKED,
                match={"key_id": key_id},
                timeout_s=8.0,
                ready_event=ready,
            )
        )
        await ready.wait()
        rc = await delete_key(jwt, key_id)
        if rc not in (200, 204):
            listener.cancel()
            return {"name": name, "pass": False,
                    "reason": f"DELETE /api-keys/{{id}} returned {rc}",
                    "key_id": key_id}

        found, latency_s, evt = await listener
        return {
            "name": name, "pass": bool(found),
            "latency_s": latency_s, "key_id": key_id,
            "delete_status": rc,
            "evt": evt if found else None,
            "reason": None if found else "no key_revoked event in 8 s",
        }
    except Exception as exc:
        return {"name": name, "pass": False,
                "reason": f"exception: {type(exc).__name__}: {exc}"}


# ─────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────


def _print_result(r: dict) -> None:
    status = "PASS" if r.get("pass") else "FAIL"
    name = r.get("name", "?")
    if r.get("pass"):
        print(f"  [{status}] {name}  latency={r.get('latency_s')}s", flush=True)
    else:
        print(f"  [{status}] {name}  reason={r.get('reason')}", flush=True)


async def main() -> int:
    print("→ Minting Clerk JWT…", flush=True)
    try:
        jwt = mint_jwt()
    except Exception as exc:
        print(f"FATAL: Clerk JWT mint failed: {type(exc).__name__}: {exc}", flush=True)
        return 2

    print(f"→ Probing {GW}/events/stream for three new event types\n", flush=True)
    results: list[dict] = []

    # Run scenarios sequentially — each opens its own subscriber and
    # the order doesn't matter, but serialising keeps the print output
    # readable.
    for fn in (scenario_A_approval_resolved,
               scenario_B_policy_decision_deny,
               scenario_C_key_revoked):
        try:
            r = await fn(jwt)
        except Exception as exc:
            r = {"name": fn.__name__, "pass": False,
                 "reason": f"orchestrator exception: {type(exc).__name__}: {exc}"}
        results.append(r)
        _print_result(r)

    n_pass = sum(1 for r in results if r.get("pass"))
    print(f"\n=== {n_pass}/{len(results)} scenarios PASS ===", flush=True)
    return 0 if n_pass == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
