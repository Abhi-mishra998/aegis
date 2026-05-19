"""
Universal ACP Wrapper
======================
One class that works with ANY framework or HTTP client.
Copy-paste this into your project and call acp.guard() before any tool runs.

Works with: LangChain, LlamaIndex, AutoGen, CrewAI, Haystack,
            plain Python, FastAPI endpoints, or any REST client.
"""

from __future__ import annotations

import functools
import os
import time
from collections.abc import Callable
from typing import Any

import requests


class ACP:
    """
    Drop-in ACP guard for any AI agent project.

    Usage:
        acp = ACP.from_env()                    # reads env vars
        acp.guard("read_file", {"path": "/x"})  # raises on deny
        result = acp.run("read_file", fn, path="/x")  # guard + execute
        @acp.protect(tool="read_file")           # decorator
        def read_file(path): ...
    """

    def __init__(self, base_url: str, token: str, tenant_id: str, agent_id: str) -> None:
        self.base_url  = base_url.rstrip("/")
        self.agent_id  = agent_id
        self._headers  = {
            "Authorization": f"Bearer {token}",
            "X-Tenant-ID":   tenant_id,
            "X-Agent-ID":    agent_id,
        }

    @classmethod
    def from_env(cls) -> ACP:
        """Read config from environment variables."""
        return cls(
            base_url  = os.environ["ACP_BASE_URL"],
            token     = os.environ["ACP_TOKEN"],
            tenant_id = os.environ["ACP_TENANT_ID"],
            agent_id  = os.environ["ACP_AGENT_ID"],
        )

    # ── Core method ──────────────────────────────────────────────────────────

    def guard(
        self,
        tool: str,
        parameters: dict[str, Any],
        *,
        tokens: int = 100,
        task: str = "",
        raise_on_deny: bool = True,
    ) -> dict[str, Any]:
        """
        Check with ACP whether this tool call is allowed.

        Returns the decision dict on allow.
        Raises PermissionError on deny (unless raise_on_deny=False).

        Parameters
        ----------
        tool        : The tool name. Must match what's in the agent's allowlist.
        parameters  : The tool's input parameters. ACP scans these for risk.
        tokens      : Estimated token cost — feeds billing and cost signal.
        task        : Optional label shown in audit trail.
        raise_on_deny: If False, returns {"allowed": False, "reason": ...} instead.
        """
        resp = requests.post(
            f"{self.base_url}/execute/{tool}",
            headers=self._headers,
            json={
                "parameters": parameters,
                "metadata": {"tokens": tokens, "task": task},
            },
            timeout=10,
        )

        if resp.status_code == 200:
            body = resp.json()
            body["allowed"] = True
            return body

        if resp.status_code == 403:
            body = resp.json()
            reason = body.get("error") or body.get("detail", "denied")
            if raise_on_deny:
                raise PermissionError(f"[ACP] {tool} denied: {reason}")
            return {"allowed": False, "reason": reason, "tool": tool}

        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 1))
            raise RuntimeError(f"[ACP] rate limited on {tool}. Retry in {retry_after}s")

        if resp.status_code == 504:
            raise TimeoutError(f"[ACP] decision pipeline timeout for {tool}")

        resp.raise_for_status()
        return {}

    # ── Convenience: guard + execute in one call ──────────────────────────────

    def run(
        self,
        tool: str,
        fn: Callable,
        *args: Any,
        acp_tokens: int = 100,
        acp_task: str = "",
        **kwargs: Any,
    ) -> Any:
        """
        Guard a tool call then execute it if allowed.

        Example:
            result = acp.run("read_file", open_file, path="/data/x.csv")
        """
        self.guard(tool, kwargs or {"args": list(args)}, tokens=acp_tokens, task=acp_task)
        return fn(*args, **kwargs)

    # ── Decorator ─────────────────────────────────────────────────────────────

    def protect(self, tool: str | None = None, tokens: int = 100):
        """
        Decorator that guards a function with ACP.

        @acp.protect(tool="read_file")
        def read_file(path: str) -> str:
            return open(path).read()
        """
        def decorator(fn: Callable) -> Callable:
            tool_name = tool or fn.__name__

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                self.guard(tool_name, kwargs or {"args": list(args)}, tokens=tokens)
                return fn(*args, **kwargs)

            return wrapper
        return decorator

    # ── Retry helper ──────────────────────────────────────────────────────────

    def guard_with_retry(
        self,
        tool: str,
        parameters: dict,
        *,
        max_retries: int = 3,
        tokens: int = 100,
    ) -> dict:
        """Like guard() but retries on rate-limit with backoff."""
        for attempt in range(max_retries):
            try:
                return self.guard(tool, parameters, tokens=tokens)
            except RuntimeError as e:
                if "rate limited" in str(e) and attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"  [ACP] rate limited, waiting {wait}s (attempt {attempt+1})")
                    time.sleep(wait)
                else:
                    raise
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# EXAMPLES — how to use with different frameworks
# ─────────────────────────────────────────────────────────────────────────────

# ── Pattern 1: Plain Python ───────────────────────────────────────────────────
def plain_python_example(acp: ACP) -> None:
    print("\n── Plain Python ──")

    @acp.protect(tool="read_file")
    def read_file(path: str) -> str:
        return open(path).read() if os.path.exists(path) else f"not found: {path}"

    @acp.protect(tool="db_query")
    def query(sql: str) -> list:
        return [{"row": sql}]  # replace with real DB call

    try:
        content = read_file("/data/report.csv")
        print(f"  read_file → {content[:50]}")
    except PermissionError as e:
        print(f"  blocked → {e}")

    try:
        rows = query("SELECT * FROM orders LIMIT 5")
        print(f"  db_query → {rows}")
    except PermissionError as e:
        print(f"  blocked → {e}")


# ── Pattern 2: FastAPI endpoint ───────────────────────────────────────────────
def fastapi_example(acp: ACP) -> None:
    """
    In a FastAPI endpoint, call acp.guard() before the action:

    @app.post("/agent/action")
    async def agent_action(request: ActionRequest, current_user: User = Depends(get_user)):
        # User's AI agent is requesting to perform an action
        try:
            decision = acp.guard(
                tool=request.tool,
                parameters=request.parameters,
                tokens=request.estimated_tokens,
                task=request.task_description,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))

        # ACP allowed — run the real action
        result = await execute_real_tool(request.tool, request.parameters)
        return {"result": result, "risk": decision["risk"]}
    """
    print("\n── FastAPI pattern (see docstring) ──")


# ── Pattern 3: LlamaIndex ─────────────────────────────────────────────────────
def llamaindex_example(acp: ACP) -> None:
    """
    Wrap a LlamaIndex FunctionTool:

    from llama_index.core.tools import FunctionTool

    def read_file_safe(path: str) -> str:
        acp.guard("read_file", {"path": path})   # raises PermissionError on deny
        return open(path).read()

    tool = FunctionTool.from_defaults(fn=read_file_safe, name="read_file")
    """
    print("\n── LlamaIndex pattern (see docstring) ──")


# ── Pattern 4: AutoGen ────────────────────────────────────────────────────────
def autogen_example(acp: ACP) -> None:
    """
    Wrap AutoGen tool functions:

    import autogen

    def read_file(path: str) -> str:
        acp.guard("read_file", {"path": path})
        return open(path).read()

    assistant = autogen.AssistantAgent("assistant", llm_config=llm_config)
    user = autogen.UserProxyAgent("user", code_execution_config=False)
    user.register_function({"read_file": read_file})
    """
    print("\n── AutoGen pattern (see docstring) ──")


# ── Pattern 5: CrewAI ────────────────────────────────────────────────────────
def crewai_example(acp: ACP) -> None:
    """
    Wrap a CrewAI tool:

    from crewai.tools import BaseTool

    class ReadFileTool(BaseTool):
        name = "read_file"
        description = "Read a file"

        def _run(self, path: str) -> str:
            acp.guard("read_file", {"path": path})   # ACP check
            return open(path).read()                  # real execution

    agent = Agent(role="analyst", tools=[ReadFileTool()])
    """
    print("\n── CrewAI pattern (see docstring) ──")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Option A: from environment variables
    # export ACP_BASE_URL=http://localhost:8000
    # export ACP_TOKEN=your-jwt-token
    # export ACP_TENANT_ID=00000000-0000-0000-0000-000000000001
    # export ACP_AGENT_ID=your-agent-uuid
    # acp = ACP.from_env()

    # Option B: explicit
    acp = ACP(
        base_url  = "http://localhost:8000",
        token     = os.environ.get("ACP_TOKEN", "your-token"),
        tenant_id = "00000000-0000-0000-0000-000000000001",
        agent_id  = os.environ.get("ACP_AGENT_ID", "your-agent-id"),
    )

    plain_python_example(acp)
    fastapi_example(acp)
    llamaindex_example(acp)
    autogen_example(acp)
    crewai_example(acp)
