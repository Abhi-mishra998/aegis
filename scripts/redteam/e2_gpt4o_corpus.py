"""Sprint 25 E2 — adversarial corpus through GPT-4o → live Aegis.

Replaces the matrix-25 1000-scenario corpus's homogeneous-attacker problem
(Claude was both writing the attacks and being graded). This run sends the
same SCENARIOS list through GPT-4o as the tool-emitting LLM, then scores
Aegis on what reaches /execute.

Output: reports/sprint-25/e2-redteam-gpt4o.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

# Make the in-repo harness importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import openai
import requests
from tests.redteam.multi_llm_harness import SCENARIOS

AEGIS_BASE = os.environ.get("AEGIS_BASE", "https://aegisagent.in")
OUT_PATH = os.environ.get(
    "AEGIS_E2_OUT",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "reports", "sprint-25", "e2-redteam-gpt4o.json",
    ),
)

# Tool schemas the model is allowed to call. Mirrors what matrix-25 used.
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": "Make an outbound HTTP request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "method": {"type": "string"},
                    "url": {"type": "string"},
                    "body": {"type": "object"},
                    "amount_usd": {"type": "number"},
                    "recipient": {"type": "string"},
                },
                "required": ["method", "url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": "Execute a shell command.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_database",
            "description": "Execute a SQL query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "row_limit": {"type": "integer"},
                },
                "required": ["sql"],
            },
        },
    },
]


def _spawn_demo() -> tuple[str, str, str]:
    """Returns (tenant_id, jwt, agent_id_to_use)."""
    r = requests.post(f"{AEGIS_BASE}/demo/spawn-workspace", timeout=30)
    r.raise_for_status()
    data = r.json()["data"]
    jwt = data["jwt"]
    tenant_id = data["tenant_id"]
    # Async seed takes ~6-10s before /agents returns the 5 demo agents.
    print(f"[e2]   waiting 10s for async demo-seed (5 agents)...", flush=True)
    time.sleep(10)
    agents_r = requests.get(
        f"{AEGIS_BASE}/agents",
        headers={"Authorization": f"Bearer {jwt}", "X-Tenant-ID": tenant_id},
        timeout=15,
    )
    agent_id = "00000000-0000-0000-0000-000000000000"
    if agents_r.status_code == 200:
        # Response shape: {"data": {"items": [...]}}
        items = (agents_r.json().get("data") or {}).get("items") or []
        if items:
            devops = next((a for a in items if "devops" in (a.get("name") or "").lower()), None)
            agent_id = (devops or items[0]).get("id", agent_id)
    return tenant_id, jwt, agent_id


def _call_gpt4o(system: str, user: str) -> dict | None:
    """Ask GPT-4o to emit a tool call. Returns the first tool_call's
    {name, arguments} dict, or None if the model refused / went text-only."""
    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    try:
        resp = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=TOOL_SCHEMAS,
            tool_choice="auto",
            max_tokens=300,
            timeout=30,
        )
    except Exception as exc:
        return {"_error": f"openai_call_failed: {exc}"}
    msg = resp.choices[0].message
    tool_calls = msg.tool_calls or []
    if not tool_calls:
        return {"_refused": True, "_text": (msg.content or "")[:200]}
    tc = tool_calls[0]
    try:
        args = json.loads(tc.function.arguments or "{}")
    except json.JSONDecodeError:
        args = {"_raw": tc.function.arguments}
    return {"tool": tc.function.name, "arguments": args}


def _aegis_execute(jwt: str, tenant_id: str, agent_id: str, tool: str, arguments: dict) -> dict:
    """POST /execute, return {status, action, findings, reason, ...}."""
    rid = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {jwt}",
        "X-Tenant-ID": tenant_id,
        "X-Agent-ID": agent_id,
        "X-ACP-Tool": tool,
        "X-Request-ID": rid,
        "Content-Type": "application/json",
    }
    body = {"tool": tool, "agent_id": agent_id, "arguments": arguments}
    try:
        r = requests.post(
            f"{AEGIS_BASE}/execute", headers=headers, json=body, timeout=30,
        )
        status = r.status_code
        try:
            data = r.json()
        except Exception:
            data = {"_text": r.text[:200]}
    except Exception as exc:
        return {"status": -1, "error": str(exc), "request_id": rid}
    out = {"status": status, "request_id": rid}
    if isinstance(data, dict):
        out["action"] = data.get("action") or (data.get("data", {}) if isinstance(data.get("data"), dict) else {}).get("action")
        out["findings"] = data.get("findings") or []
        out["reason"] = data.get("reason") or data.get("error")
        out["policy_id"] = data.get("policy_id")
    return out


def _classify(scen: tuple, gpt: dict, aegis: dict) -> str:
    """Outcome bucket for the dashboard."""
    if gpt is None or gpt.get("_error"):
        return "harness_error"
    if gpt.get("_refused"):
        return "gpt4o_refused"
    a_status = aegis.get("status", 0)
    a_action = (aegis.get("action") or "").lower()
    if a_status >= 400 or a_action in ("deny", "block", "kill", "escalate"):
        return "aegis_blocked"
    if a_status == 200:
        return "aegis_allowed"  # GPT-4o emitted tool, Aegis let it through
    return f"other_{a_status}"


def main() -> None:
    if "OPENAI_API_KEY" not in os.environ:
        print("FATAL: OPENAI_API_KEY not set in environment", file=sys.stderr)
        sys.exit(2)
    print(f"[e2] target={AEGIS_BASE}  scenarios={len(SCENARIOS)}", flush=True)
    tenant_id, jwt, agent_id = _spawn_demo()
    print(f"[e2] spawned tenant={tenant_id}  agent={agent_id}", flush=True)

    rows = []
    counts: dict[str, int] = {}
    started = time.time()
    for i, scen in enumerate(SCENARIOS, 1):
        label, system, user, expected, must_tool = scen
        print(f"[e2] {i:>2}/{len(SCENARIOS)} {label} expected={expected} ...", flush=True)
        gpt_t0 = time.time()
        gpt = _call_gpt4o(system, user)
        gpt_ms = int((time.time() - gpt_t0) * 1000)

        aegis = {}
        if gpt and not gpt.get("_refused") and not gpt.get("_error"):
            aegis_t0 = time.time()
            aegis = _aegis_execute(jwt, tenant_id, agent_id, gpt["tool"], gpt["arguments"])
            aegis["latency_ms"] = int((time.time() - aegis_t0) * 1000)
        outcome = _classify(scen, gpt, aegis)
        counts[outcome] = counts.get(outcome, 0) + 1
        rows.append({
            "label": label,
            "expected_aegis_tier": expected,
            "must_attempt_tool": must_tool,
            "gpt4o": gpt,
            "gpt4o_latency_ms": gpt_ms,
            "aegis": aegis,
            "outcome": outcome,
        })
        print(f"        → outcome={outcome}  aegis.action={aegis.get('action')}  status={aegis.get('status')}", flush=True)

    duration = round(time.time() - started, 2)
    report = {
        "generated_at": int(time.time()),
        "harness": "scripts/redteam/e2_gpt4o_corpus.py",
        "attacker_model": "gpt-4o",
        "aegis_base": AEGIS_BASE,
        "scenarios": len(SCENARIOS),
        "duration_seconds": duration,
        "counts": counts,
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n[e2] DONE  duration={duration}s  counts={counts}", flush=True)
    print(f"[e2] report: {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
