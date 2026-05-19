"""
ACP + OpenAI Function Calling Integration
==========================================
OpenAI GPT models with ACP governing every function call.

Install:
    pip install openai requests
"""

from __future__ import annotations

import json
import os

import requests
from openai import OpenAI

# ─────────────────────────────────────────────────────────────────────────────
# ACP CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class ACPClient:
    def __init__(self, base_url: str, token: str, tenant_id: str, agent_id: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers  = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   tenant_id,
            "X-Agent-ID":    agent_id,
        }

    def check(self, tool_name: str, parameters: dict) -> bool:
        resp = requests.post(
            f"{self.base_url}/execute/{tool_name}",
            headers=self.headers,
            json={"parameters": parameters, "metadata": {"tokens": 100}},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        print(f"  [ACP] {tool_name} blocked: {resp.json().get('error', resp.status_code)}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TOOLS
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from disk",
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
            "name": "db_query",
            "description": "Run a SQL query",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
]

TOOL_FUNCTIONS = {
    "read_file":  lambda path: open(path).read() if os.path.exists(path) else f"not found: {path}",
    "db_query":   lambda sql: f"[rows for: {sql}]",
    "write_file": lambda path, content: (open(path, "w").write(content), f"written: {path}")[1],
}


# ─────────────────────────────────────────────────────────────────────────────
# AGENT
# ─────────────────────────────────────────────────────────────────────────────

class OpenAIACPAgent:
    def __init__(self, acp: ACPClient, model: str = "gpt-4o-mini") -> None:
        self.acp    = acp
        self.model  = model
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def _run_tool(self, name: str, args: dict) -> str:
        print(f"  → GPT wants: {name}({args})")

        if not self.acp.check(name, args):
            return f"[Blocked by ACP. I cannot perform '{name}' with these parameters.]"

        fn = TOOL_FUNCTIONS.get(name)
        result = fn(**args) if fn else f"[unknown tool: {name}]"
        print(f"  ✓ ran: {str(result)[:80]}")
        return str(result)

    def run(self, user_message: str) -> str:
        print(f"\nUser: {user_message}")
        messages = [{"role": "user", "content": user_message}]

        for _ in range(10):
            response = self.client.chat.completions.create(
                model    = self.model,
                messages = messages,
                tools    = TOOLS,
            )

            msg = response.choices[0].message
            messages.append(msg)

            if not msg.tool_calls:
                return msg.content or ""

            for tc in msg.tool_calls:
                args   = json.loads(tc.function.arguments)
                result = self._run_tool(tc.function.name, args)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      result,
                })

        return "Max iterations"


# ─────────────────────────────────────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    acp = ACPClient(
        base_url  = os.environ.get("ACP_BASE_URL",  "http://localhost:8000"),
        token     = os.environ.get("ACP_TOKEN",     "your-jwt-token"),
        tenant_id = os.environ.get("ACP_TENANT_ID", "00000000-0000-0000-0000-000000000001"),
        agent_id  = os.environ.get("ACP_AGENT_ID",  "your-agent-uuid"),
    )

    agent = OpenAIACPAgent(acp=acp)

    answer = agent.run("Read /data/report.csv and tell me what's in it")
    print(f"\nGPT: {answer}")
