"""
aegis-langchain — LangChain governance middleware for Aegis ACP
Install: pip install aegis-langchain  (or pip install -e integrations/aegis-langchain)

Usage:
    from aegis_langchain import AegisMiddleware

    agent = AegisMiddleware(
        my_langchain_agent,
        api_key="acp_...",        # or set AEGIS_API_KEY env var
        tenant_id="...",          # or set AEGIS_TENANT_ID env var
        agent_id="...",           # optional
    )
    result = agent.invoke({"input": "analyze the /etc/passwd file"})
    # → tool calls are checked by Aegis before execution; blocked = descriptive message
"""
from __future__ import annotations

import functools
import json
import os
from typing import Any

import httpx

__version__ = "1.1.1"
__all__ = ["AegisMiddleware", "AegisCallbackHandler", "AegisClient", "__version__"]


class AegisClient:
    """Thin synchronous client for the Aegis /execute governance endpoint."""

    def __init__(
        self,
        api_key: str,
        gateway_url: str = "https://aegisagent.in",
        tenant_id: str = "",
        agent_id: str = "langchain-agent",
        timeout: float = 10.0,
    ) -> None:
        self._url = gateway_url.rstrip("/")
        self._agent_id = agent_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "X-Tenant-ID": tenant_id,
            "X-Agent-ID": agent_id,
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    def check(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(
                    f"{self._url}/execute",
                    headers=self._headers,
                    json={
                        # Canonical field names — `tool_name`/`parameters`
                        # are accepted as a fallback but the audit row gets
                        # logged with tool="unknown". Use the right names.
                        "agent_id": self._agent_id,
                        "tool":      tool_name,
                        "arguments": parameters,
                    },
                )
            if resp.status_code in (200, 403):
                # Sprint B follow-up 2026-06-14 — WAFv2 returns text/html on
                # sensitive-path blocks; resp.json() would raise. Surface as
                # waf_blocked so the agent sees a real reason.
                ctype = (resp.headers.get("content-type") or "").lower()
                if "html" in ctype or "json" not in ctype:
                    if resp.status_code == 403:
                        return {"action": "deny", "risk": 1.0,
                                "findings": ["waf_blocked"]}
                try:
                    return self._normalize(resp.json())
                except Exception:
                    if resp.status_code == 403:
                        return {"action": "deny", "risk": 1.0,
                                "findings": ["waf_blocked"]}
                    raise
            # Fail CLOSED on transport / server errors.
            return {
                "action":   "deny",
                "risk":     1.0,
                "findings": [f"aegis_http_{resp.status_code}"],
            }
        except Exception as exc:
            return {
                "action":   "deny",
                "risk":     1.0,
                "findings": [f"aegis_error:{type(exc).__name__}"],
            }

    @staticmethod
    def _normalize(body: dict[str, Any]) -> dict[str, Any]:
        """Map every /execute response onto {action, risk, findings}.

        Plain 200 success → unwrap data envelope. 403 with
        `approval_required` → escalate. 403 with any other error and no
        action field → deny + carry the error string. Anything else with
        success=false → fail-closed deny.
        """
        data = body.get("data") if isinstance(body, dict) else None
        if data is None and isinstance(body, dict) and body.get("action"):
            data = body
        if isinstance(data, dict) and data.get("action"):
            return data
        err = (body.get("error") if isinstance(body, dict) else None) or "denied"
        action = "escalate" if "approval_required" in str(err).lower() else "deny"
        return {"action": action, "risk": 1.0, "findings": [str(err)[:120]]}

    def is_blocked(self, decision: dict[str, Any]) -> bool:
        return decision.get("action", "deny") in (
            "deny", "block", "policy_deny", "reject", "escalate",
        )

    def blocked_message(self, tool_name: str, decision: dict[str, Any]) -> str:
        findings = decision.get("findings", ["policy_violation"])
        risk = decision.get("risk", 1.0)
        return f"[BLOCKED by Aegis] Tool '{tool_name}' denied (risk={risk:.3f}): {findings}"


class AegisMiddleware:
    """
    Drop-in governance wrapper for any LangChain AgentExecutor or Runnable.

    Patches every tool in the agent so each call is checked with Aegis
    before execution. Blocked calls return a descriptive message instead
    of running — the agent sees the block and responds appropriately.
    """

    def __init__(
        self,
        agent: Any,
        api_key: str | None = None,
        gateway_url: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._agent = agent
        self._client = AegisClient(
            api_key=api_key or os.environ["AEGIS_API_KEY"],
            gateway_url=gateway_url or os.environ.get("AEGIS_URL", "https://aegisagent.in"),
            tenant_id=tenant_id or os.environ.get("AEGIS_TENANT_ID", ""),
            agent_id=agent_id or os.environ.get("AEGIS_AGENT_ID", "langchain-agent"),
            timeout=timeout,
        )
        self._patch_tools()

    def _patch_tools(self) -> None:
        tools = getattr(self._agent, "tools", None)
        if not tools:
            return
        for tool in tools:
            tool._run = self._make_governed_run(tool.name, tool._run)
            if hasattr(tool, "_arun"):
                tool._arun = self._make_governed_arun(tool.name, tool._arun)

    def _make_governed_run(self, tool_name: str, original_run: Any) -> Any:
        client = self._client

        @functools.wraps(original_run)
        def governed_run(*args: Any, **kwargs: Any) -> Any:
            params = kwargs if kwargs else ({"args": list(args)} if args else {})
            decision = client.check(tool_name, params)
            if client.is_blocked(decision):
                return client.blocked_message(tool_name, decision)
            return original_run(*args, **kwargs)

        return governed_run

    def _make_governed_arun(self, tool_name: str, original_arun: Any) -> Any:
        client = self._client

        @functools.wraps(original_arun)
        async def governed_arun(*args: Any, **kwargs: Any) -> Any:
            params = kwargs if kwargs else ({"args": list(args)} if args else {})
            decision = client.check(tool_name, params)
            if client.is_blocked(decision):
                return client.blocked_message(tool_name, decision)
            return await original_arun(*args, **kwargs)

        return governed_arun

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        return self._agent.invoke(*args, **kwargs)

    def stream(self, *args: Any, **kwargs: Any) -> Any:
        return self._agent.stream(*args, **kwargs)

    async def ainvoke(self, *args: Any, **kwargs: Any) -> Any:
        return await self._agent.ainvoke(*args, **kwargs)

    async def astream(self, *args: Any, **kwargs: Any) -> Any:
        async for chunk in self._agent.astream(*args, **kwargs):
            yield chunk

    def __getattr__(self, name: str) -> Any:
        return getattr(self._agent, name)


class AegisCallbackHandler:
    """
    LangChain callback handler for monitor-only mode (no blocking).
    Every tool call is logged to Aegis for observability without enforcement.

    Usage:
        agent.invoke(input, config={"callbacks": [AegisCallbackHandler(api_key="acp_...")]})
    """

    def __init__(
        self,
        api_key: str | None = None,
        gateway_url: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._client = AegisClient(
            api_key=api_key or os.environ["AEGIS_API_KEY"],
            gateway_url=gateway_url or os.environ.get("AEGIS_URL", "https://aegisagent.in"),
            tenant_id=tenant_id or os.environ.get("AEGIS_TENANT_ID", ""),
            agent_id=agent_id or os.environ.get("AEGIS_AGENT_ID", "langchain-agent"),
        )

    def on_tool_start(self, serialized: dict, input_str: str, **_kwargs: Any) -> None:
        tool_name = serialized.get("name", "unknown")
        try:
            params = json.loads(input_str) if isinstance(input_str, str) else {"input": input_str}
        except Exception:
            params = {"input": str(input_str)}
        self._client.check(tool_name, params)

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        pass

    def on_tool_error(self, error: Exception, **kwargs: Any) -> None:
        pass
