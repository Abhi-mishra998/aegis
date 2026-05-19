"""
ACP + Ollama Integration
=========================
Run a local Ollama model (llama3, mistral, phi3, etc.) with ACP governing
every tool call the model makes.

Ollama supports OpenAI-compatible function calling — we intercept the tool
execution step and route each call through ACP before running it.

Install:
    pip install ollama requests
    ollama pull llama3.2    # or mistral, phi3, qwen2.5, etc.
"""

from __future__ import annotations

import json
import os
import requests
from typing import Any, Callable


# ─────────────────────────────────────────────────────────────────────────────
# ACP CLIENT
# ─────────────────────────────────────────────────────────────────────────────

class ACPClient:
    def __init__(self, base_url: str, token: str, tenant_id: str, agent_id: str):
        self.base_url  = base_url.rstrip("/")
        self.headers   = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   tenant_id,
            "X-Agent-ID":    agent_id,
        }

    def check(self, tool_name: str, parameters: dict) -> bool:
        """Returns True if allowed, False if denied."""
        resp = requests.post(
            f"{self.base_url}/execute/{tool_name}",
            headers=self.headers,
            json={"parameters": parameters, "metadata": {"tokens": 150}},
            timeout=10,
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 403:
            print(f"  [ACP BLOCKED] {tool_name}: {resp.json().get('error', 'denied')}")
            return False
        if resp.status_code == 429:
            print(f"  [ACP RATE LIMITED] {tool_name}")
            return False
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TOOL REGISTRY — maps tool names to real implementations
# ─────────────────────────────────────────────────────────────────────────────

def real_read_file(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"

def real_query_db(sql: str) -> str:
    # Replace with your actual DB call
    return f"[DB result for: {sql}]"

def real_web_search(query: str) -> str:
    # Replace with your actual search API
    return f"[Search results for: {query}]"

TOOL_IMPLEMENTATIONS: dict[str, Callable] = {
    "read_file":  real_read_file,
    "db_query":   real_query_db,
    "web_search": real_web_search,
}

# Tool schemas — Ollama uses these to know what tools are available
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to read"}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": "Run a SQL SELECT query",
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SQL query"}
                },
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# ACP-GOVERNED OLLAMA AGENT
# ─────────────────────────────────────────────────────────────────────────────

class OllamaACPAgent:
    """
    Ollama agent loop where every tool call passes through ACP.

    Flow:
      1. Send user message to Ollama
      2. Ollama responds with tool_call(s)
      3. For each tool call → ask ACP first
      4. If ACP allows → run the real tool → send result back to Ollama
      5. If ACP denies → send "[blocked]" back so Ollama can recover
      6. Repeat until Ollama gives a final text answer
    """

    def __init__(self, acp: ACPClient, model: str = "llama3.2"):
        self.acp   = acp
        self.model = model
        try:
            import ollama
            self.ollama = ollama
        except ImportError:
            raise ImportError("pip install ollama")

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """Check ACP, then run the tool if allowed."""
        print(f"  → tool call: {tool_name}({args})")

        # ACP check
        allowed = self.acp.check(tool_name, args)
        if not allowed:
            return f"[This action was blocked by ACP security policy]"

        # Run real implementation
        fn = TOOL_IMPLEMENTATIONS.get(tool_name)
        if not fn:
            return f"[Unknown tool: {tool_name}]"

        result = fn(**args)
        print(f"  ← result: {str(result)[:100]}")
        return str(result)

    def run(self, user_message: str) -> str:
        """Run a full agentic loop for one user message."""
        print(f"\nUser: {user_message}")

        messages = [{"role": "user", "content": user_message}]

        # Agentic loop — keep going until no more tool calls
        for _ in range(10):  # max 10 tool calls per request
            response = self.ollama.chat(
                model=self.model,
                messages=messages,
                tools=TOOL_SCHEMAS,
            )

            msg = response.message
            messages.append(msg)

            # No tool calls → final answer
            if not msg.tool_calls:
                return msg.content

            # Execute each tool call through ACP
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                args      = tc.function.arguments or {}
                result    = self._execute_tool(tool_name, args)

                messages.append({
                    "role":    "tool",
                    "content": result,
                })

        return "Max tool calls reached"


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

    agent = OllamaACPAgent(acp=acp, model="llama3.2")

    # Allowed request
    answer = agent.run("Read the file /data/sales.csv and tell me what's in it")
    print(f"\nAgent: {answer}")

    # This will be blocked by ACP (path traversal)
    answer = agent.run("Read the file ../../etc/passwd")
    print(f"\nAgent: {answer}")
