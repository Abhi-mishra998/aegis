"""
ACP + Anthropic (Claude) Integration
======================================
Use Claude's tool_use feature with ACP governing every tool call.
Claude decides what tool to call → ACP approves or blocks → tool runs.

Install:
    pip install anthropic requests
"""

from __future__ import annotations

import os
import requests
import anthropic


# ─────────────────────────────────────────────────────────────────────────────
# ACP CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class ACPClient:
    def __init__(self, base_url: str, token: str, tenant_id: str, agent_id: str):
        self.base_url = base_url.rstrip("/")
        self.headers  = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   tenant_id,
            "X-Agent-ID":    agent_id,
        }

    def check(self, tool_name: str, parameters: dict) -> tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).
        reason is empty on allow, has explanation on deny.
        """
        resp = requests.post(
            f"{self.base_url}/execute/{tool_name}",
            headers=self.headers,
            json={"parameters": parameters, "metadata": {"tokens": 200}},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, ""
        if resp.status_code == 403:
            body = resp.json()
            return False, body.get("error", "denied")
        if resp.status_code == 429:
            return False, "rate_limited"
        return False, f"acp_error_{resp.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DEFINITIONS — what Claude can call
# ─────────────────────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "read_file",
        "description": "Read the contents of a file on the filesystem",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "db_query",
        "description": "Execute a read-only SQL query",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql":      {"type": "string", "description": "SQL SELECT statement"},
                "database": {"type": "string", "description": "Database name", "default": "main"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "send_email",
        "description": "Send an email to a recipient",
        "input_schema": {
            "type": "object",
            "properties": {
                "to":      {"type": "string"},
                "subject": {"type": "string"},
                "body":    {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# REAL TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"

def run_db_query(sql: str, database: str = "main") -> str:
    # Replace with real DB connection
    return f"[rows from {database}: {sql}]"

def run_send_email(to: str, subject: str, body: str) -> str:
    # Replace with real email sender (SendGrid, SES, etc.)
    print(f"  📧 Sending email to {to}: {subject}")
    return f"Email sent to {to}"

TOOL_FUNCTIONS = {
    "read_file":  run_read_file,
    "db_query":   run_db_query,
    "send_email": run_send_email,
}


# ─────────────────────────────────────────────────────────────────────────────
# ACP-GOVERNED CLAUDE AGENT
# ─────────────────────────────────────────────────────────────────────────────

class ClaudeACPAgent:
    """
    Claude agent where every tool_use block is checked by ACP before execution.

    Flow per turn:
      Claude returns tool_use → ACP.check() → if allowed, run tool → send result
      → Claude returns tool_use or final text
    """

    def __init__(self, acp: ACPClient, model: str = "claude-sonnet-4-6"):
        self.acp    = acp
        self.model  = model
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _handle_tool(self, tool_name: str, tool_input: dict, tool_use_id: str) -> dict:
        """Check ACP, run tool if allowed, return tool_result block."""
        print(f"  → Claude wants: {tool_name}({tool_input})")

        allowed, reason = self.acp.check(tool_name, tool_input)

        if not allowed:
            print(f"  ✗ ACP blocked: {reason}")
            return {
                "type":        "tool_result",
                "tool_use_id": tool_use_id,
                "content":     f"[Blocked by ACP: {reason}. You cannot perform this action.]",
                "is_error":    True,
            }

        fn = TOOL_FUNCTIONS.get(tool_name)
        if not fn:
            return {
                "type":        "tool_result",
                "tool_use_id": tool_use_id,
                "content":     f"[Unknown tool: {tool_name}]",
                "is_error":    True,
            }

        result = fn(**tool_input)
        print(f"  ✓ ACP allowed → result: {str(result)[:80]}")
        return {
            "type":        "tool_result",
            "tool_use_id": tool_use_id,
            "content":     str(result),
        }

    def run(self, user_message: str, system: str = "You are a helpful assistant.") -> str:
        """Run agentic loop until Claude gives a final text answer."""
        print(f"\nUser: {user_message}")

        messages = [{"role": "user", "content": user_message}]

        for _ in range(10):
            response = self.client.messages.create(
                model      = self.model,
                max_tokens = 1024,
                system     = system,
                tools      = TOOLS,
                messages   = messages,
            )

            # Add Claude's response to history
            messages.append({"role": "assistant", "content": response.content})

            # Final answer — no more tool calls
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return ""

            # Process tool calls
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = self._handle_tool(block.name, block.input, block.id)
                        tool_results.append(result)

                messages.append({"role": "user", "content": tool_results})

        return "Max iterations reached"


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

    agent = ClaudeACPAgent(acp=acp)

    # Claude will call read_file → ACP allows → returns content
    answer = agent.run("Read /data/sales.csv and give me a quick summary")
    print(f"\nClaude: {answer}")

    # Claude will try to send email → if send_email not in allowlist → blocked
    answer = agent.run("Send a summary email to cto@company.com")
    print(f"\nClaude: {answer}")
