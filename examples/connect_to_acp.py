"""
HOW TO CONNECT YOUR PROJECT TO ACP
===================================
This file shows three ways to integrate, from simplest to most complete.

  1. Raw HTTP  — no SDK, just requests. Good for any language/framework.
  2. SDK       — Python @acp.protect decorator. The recommended production path.
  3. Full agent loop — realistic example where a user gives a task and the
                       agent executes it step by step through ACP.

Prerequisites:
  pip install requests httpx
  ACP stack running: cd infra && docker compose up -d
"""

from __future__ import annotations

import os
import time

import requests  # pip install requests

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG — change these to match your ACP deployment
# ─────────────────────────────────────────────────────────────────────────────

ACP_BASE_URL = os.environ.get("ACP_BASE_URL", "http://localhost:8000")
ACP_EMAIL    = os.environ.get("ACP_EMAIL",    "admin@acp.local")
ACP_PASSWORD = os.environ.get("ACP_PASSWORD", "password")
TENANT_ID    = os.environ.get("ACP_TENANT_ID", "00000000-0000-0000-0000-000000000001")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Login and get a JWT token
# Every request to ACP needs this token in Authorization: Bearer <token>
# ─────────────────────────────────────────────────────────────────────────────

def login() -> str:
    resp = requests.post(
        f"{ACP_BASE_URL}/auth/token",
        headers={"X-Tenant-ID": TENANT_ID},
        json={"email": ACP_EMAIL, "password": ACP_PASSWORD},
    )
    resp.raise_for_status()
    token = resp.json()["data"]["access_token"]
    print(f"[auth] logged in, token={token[:20]}...")
    return token


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Register your agent with ACP
# An agent is a named entity that represents your AI bot/worker.
# Do this ONCE when you deploy — not on every request.
# ─────────────────────────────────────────────────────────────────────────────

def register_agent(token: str, agent_name: str) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID": TENANT_ID,
    }

    # Try to create. If agent already exists, fetch the existing one.
    resp = requests.post(
        f"{ACP_BASE_URL}/agents",
        headers=headers,
        json={
            "name": agent_name,
            "description": "My AI agent that reads files and queries data",
            "owner_team": "engineering",
            "framework": "custom",
            "risk_level": "low",             # low / medium / high
            "created_by": "00000000-0000-0000-0000-000000000001",
        },
    )

    data = resp.json()
    if data.get("data", {}).get("id"):
        agent_id = data["data"]["id"]
        print(f"[agent] created: {agent_id}")
    else:
        # Already exists — fetch it
        resp = requests.get(
            f"{ACP_BASE_URL}/agents?limit=50",
            headers=headers,
        )
        agents = resp.json()["data"]["data"]
        match = next((a for a in agents if a["name"] == agent_name), None)
        if not match:
            raise RuntimeError(f"Agent '{agent_name}' not found after create failed")
        agent_id = match["id"]
        print(f"[agent] already exists: {agent_id}")

    return agent_id


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Grant permissions to the agent
# ACP uses a tool allowlist. If the tool is not in the list, OPA blocks it.
# ─────────────────────────────────────────────────────────────────────────────

def grant_permission(token: str, agent_id: str, tool_name: str) -> None:
    resp = requests.post(
        f"{ACP_BASE_URL}/agents/{agent_id}/permissions",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": TENANT_ID,
        },
        json={
            "tool_name": tool_name,
            "action": "ALLOW",
            "granted_by": "00000000-0000-0000-0000-000000000001",
        },
    )
    result = resp.json()
    if result.get("success") or result.get("data", {}).get("id"):
        print(f"[permission] granted: {tool_name}")
    else:
        print(f"[permission] {tool_name} already exists (idempotent): {result.get('error', '')}")


# ─────────────────────────────────────────────────────────────────────────────
# WAY 1 — RAW HTTP (no SDK, works from any language)
# This is the direct approach: call /execute/{tool_name} yourself.
# ACP evaluates the request and returns allow / deny.
# YOUR code then runs the real action based on the response.
# ─────────────────────────────────────────────────────────────────────────────

def call_tool_raw_http(token: str, agent_id: str, tool: str, parameters: dict) -> dict:
    """
    Ask ACP whether this tool call is allowed.
    Returns the decision. Raises on deny.

    What you can pass in parameters:
      - Any key/value your tool needs. ACP passes them to the policy engine
        and scans them for risk signals (path traversal, keyword risk, etc.)
      - Common patterns:
          read_file  → {"path": "/data/report.csv"}
          db.query   → {"sql": "SELECT * FROM orders LIMIT 10"}
          api.call   → {"url": "https://api.example.com/data", "method": "GET"}
          shell.exec → {"cmd": "ls /tmp"}  ← will be denied if not allowed
    """
    resp = requests.post(
        f"{ACP_BASE_URL}/execute/{tool}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID": TENANT_ID,
            "X-Agent-ID": agent_id,
        },
        json={
            "parameters": parameters,
            "metadata": {
                "tokens": 100,          # estimated token cost — feeds billing
                "task": "user request", # optional label, appears in audit log
            },
        },
    )

    if resp.status_code == 200:
        return resp.json()  # {"success": true, "action": "allow", "risk": 0.27, ...}

    if resp.status_code == 403:
        body = resp.json()
        error = body.get("error") or body.get("detail", "denied")
        raise PermissionError(f"ACP denied [{tool}]: {error}")

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "?")
        raise RuntimeError(f"Rate limited. Retry after {retry_after}s")

    if resp.status_code == 504:
        raise TimeoutError("ACP decision pipeline timed out")

    resp.raise_for_status()
    return resp.json()


def demo_raw_http(token: str, agent_id: str) -> None:
    print("\n── WAY 1: Raw HTTP ──")

    # Allowed call
    try:
        result = call_tool_raw_http(token, agent_id, "read_file", {"path": "/data/report.csv"})
        print(f"  ALLOW → risk={result.get('risk'):.2f}, findings={result.get('findings', [])}")
        # Now YOUR code does the actual file read:
        # content = open("/data/report.csv").read()
        print("  → your code would now actually read the file here")
    except PermissionError as e:
        print(f"  DENY  → {e}")

    # Denied call — path traversal
    try:
        call_tool_raw_http(token, agent_id, "read_file", {"path": "../../etc/passwd"})
    except PermissionError as e:
        print(f"  DENY  (path traversal) → {e}")

    # Denied call — tool not in allowlist
    try:
        call_tool_raw_http(token, agent_id, "shell.exec", {"cmd": "ls /tmp"})
    except PermissionError as e:
        print(f"  DENY  (not in allowlist) → {e}")


# ─────────────────────────────────────────────────────────────────────────────
# WAY 2 — SDK with @acp.protect decorator (recommended)
# The decorator wraps your real tool function.
# If ACP says ALLOW → your function body runs (real execution happens here).
# If ACP says DENY  → DeniedError is raised, your function body never runs.
# ─────────────────────────────────────────────────────────────────────────────

def demo_sdk(token: str, agent_id: str) -> None:
    print("\n── WAY 2: SDK @acp.protect ──")

    try:
        from sdk.acp_client import (
            Client,
            DeniedError,
            EscalationRequiredError,
            RateLimitedError,
        )
    except ImportError:
        print("  [skip] sdk not importable in this environment")
        return

    # Initialize SDK client with your JWT token
    acp = Client(token=token, base_url=ACP_BASE_URL)

    # Wrap your REAL tool implementations with the decorator
    @acp.protect(agent_id=agent_id, tool="read_file")
    def read_file(path: str) -> str:
        # This code only runs if ACP says ALLOW.
        # Replace with your actual implementation:
        # return open(path).read()
        return f"<contents of {path}>"

    @acp.protect(agent_id=agent_id, tool="db.query")
    def query_db(sql: str) -> list[dict]:
        # This code only runs if ACP says ALLOW.
        # Replace with your actual DB call:
        # return db.session.execute(sql).fetchall()
        return [{"row": "1", "value": sql}]

    # Allowed call
    try:
        content = read_file("/data/report.csv")
        print(f"  ALLOW read_file → {content}")
    except DeniedError as e:
        print(f"  DENY → {e.reason}: {e.detail}")

    # Allowed DB query
    try:
        rows = query_db("SELECT * FROM orders LIMIT 5")
        print(f"  ALLOW db.query → {rows}")
    except DeniedError as e:
        print(f"  DENY → {e.reason}: {e.detail}")

    # Denied — keyword "DROP" raises inference risk score
    try:
        query_db("DROP TABLE orders")
    except DeniedError as e:
        print(f"  DENY (DROP detected) → {e.reason}: {e.detail}")

    # Rate limit handling
    try:
        read_file("/data/report.csv")
    except RateLimitedError as e:
        wait = e.retry_after or 1.0
        print(f"  RATE LIMITED → waiting {wait}s then retrying")
        time.sleep(wait)

    # Approval required (when autonomy contract marks action as approval_required)
    try:
        read_file("/data/financials.csv")
    except EscalationRequiredError as e:
        print(f"  APPROVAL NEEDED → contract_id={e.contract_id}. "
              "Notify operator at /autonomy/overrides in the UI.")


# ─────────────────────────────────────────────────────────────────────────────
# WAY 3 — Full agent loop (realistic end-to-end)
# User gives a task → agent decides which tools to call → ACP governs each
# call → agent completes the task → operator can observe everything in the UI.
# ─────────────────────────────────────────────────────────────────────────────

try:
    from sdk.acp_client import RateLimitedError as RateLimitedError
except ImportError:
    class RateLimitedError(Exception):  # type: ignore[no-redef]
        retry_after: int = 60


class MyAgent:
    """
    A simple agent that accepts user tasks and executes them through ACP.
    In production this would be an LLM loop (LangChain, AutoGPT, custom).
    Here we simulate the LLM deciding what tools to call.
    """

    def __init__(self, token: str, agent_id: str) -> None:
        self.token = token
        self.agent_id = agent_id

    def _call(self, tool: str, parameters: dict) -> dict | None:
        """Call a tool through ACP. Returns result on allow, None on deny."""
        try:
            decision = call_tool_raw_http(self.token, self.agent_id, tool, parameters)
            # ACP said ALLOW. Now run the REAL action.
            # In production: return actual_file_reader(parameters["path"]) etc.
            return {"allowed": True, "decision": decision, "output": f"<result of {tool}>"}
        except PermissionError as e:
            print(f"    [blocked] {e}")
            return None
        except RateLimitedError as e:
            print(f"    [rate limited] retry after {e.retry_after}s")
            return None

    def run(self, user_task: str) -> str:
        """
        Accept a user task and execute it.
        The agent decides which tools to call (in a real system an LLM does this).
        """
        print(f"\n  User task: '{user_task}'")

        # Simulate LLM deciding what tools to call for the task
        if "sales" in user_task.lower() or "report" in user_task.lower():
            steps = [
                ("read_file", {"path": "/data/sales_2026_q1.csv"}),
                ("read_file", {"path": "/data/sales_2026_q2.csv"}),
                ("db.query",  {"sql": "SELECT SUM(revenue) FROM sales WHERE year=2026"}),
            ]
        elif "delete" in user_task.lower():
            steps = [
                ("delete_file", {"path": "/data/old_logs.txt"}),  # will be denied — not in allowlist
            ]
        else:
            steps = [
                ("read_file", {"path": "/data/general.txt"}),
            ]

        results = []
        for tool, params in steps:
            print(f"    calling {tool}({params})")
            result = self._call(tool, params)
            if result:
                results.append(result["output"])
                print("    → allowed, got output")
            else:
                results.append(f"[{tool} was blocked by ACP]")

        return f"Completed '{user_task}'. Steps: {len(steps)}, allowed: {len([r for r in results if 'blocked' not in r])}"


def demo_full_agent_loop(token: str, agent_id: str) -> None:
    print("\n── WAY 3: Full agent loop ──")

    agent = MyAgent(token=token, agent_id=agent_id)

    # User gives a task
    result = agent.run("Analyze the Q1 and Q2 sales report and give me total revenue")
    print(f"\n  Result: {result}")

    # Attempt a dangerous task — agent will be blocked
    result = agent.run("Delete old logs to free up space")
    print(f"\n  Result: {result}")


# ─────────────────────────────────────────────────────────────────────────────
# OBSERVATION — after the agent runs, the operator can observe everything
# ─────────────────────────────────────────────────────────────────────────────

def observe(token: str, agent_id: str) -> None:
    print("\n── OBSERVATION ──")
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-ID": TENANT_ID}

    # 1. Audit trail — every call logged with risk score
    resp = requests.get(f"{ACP_BASE_URL}/audit/logs?limit=5", headers=headers)
    items = resp.json().get("data", {}).get("items", [])
    print(f"  Audit trail ({len(items)} recent entries):")
    for item in items:
        print(f"    [{item.get('decision')}] {item.get('action')} "
              f"tool={item.get('tool')} "
              f"risk={item.get('metadata_json', {}).get('risk_score', '?')}")

    # 2. Flight recorder — replayable step-by-step timelines
    resp = requests.get(f"{ACP_BASE_URL}/flight/timelines?limit=3", headers=headers)
    timelines = resp.json().get("data", [])
    print(f"\n  Flight recorder ({len(timelines)} recent timelines):")
    for tl in timelines:
        print(f"    [{tl.get('final_decision')}] {tl.get('tool')} "
              f"duration={tl.get('duration_ms')}ms "
              f"steps={tl.get('step_count', '?')}")

    # 3. AI threat insights (populated within ~2s after a high-risk block)
    resp = requests.get(f"{ACP_BASE_URL}/insights/recent?limit=3", headers=headers)
    insights = resp.json().get("data", [])
    print(f"\n  AI threat insights ({len(insights)} recent):")
    for insight in insights:
        print(f"    [{insight.get('confidence')}] {insight.get('threat_classification')}: "
              f"{(insight.get('narrative') or '')[:80]}")

    print("\n  → Open http://localhost:5173 to see all of this in the UI")
    print("    Login: admin@acp.local / password")
    print("    Flight Recorder: G F | Audit Trail: G A | Observability: G O")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== Connecting to ACP ===")
    print(f"Gateway: {ACP_BASE_URL}")

    # Step 0: Login
    token = login()

    # Step 1: Register your agent (idempotent — safe to run every deploy)
    agent_id = register_agent(token, agent_name="my_ai_agent")

    # Step 2: Grant permissions (idempotent — safe to run every deploy)
    grant_permission(token, agent_id, "read_file")
    grant_permission(token, agent_id, "db.query")
    # NOTE: we do NOT grant "shell.exec" or "delete_file" — those will be denied

    # Demo 1: Raw HTTP
    demo_raw_http(token, agent_id)

    # Demo 2: SDK decorator
    demo_sdk(token, agent_id)

    # Demo 3: Full agent loop
    demo_full_agent_loop(token, agent_id)

    # Observe what happened
    time.sleep(2)  # wait for Groq insights to populate
    observe(token, agent_id)


if __name__ == "__main__":
    main()
