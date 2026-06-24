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

__version__ = "1.1.4"
__all__ = [
    "AegisMiddleware",
    "AegisCallbackHandler",
    "AegisClient",
    "AegisExecuteError",
    "AegisNetworkError",
    "__version__",
]


# ─────────────────────────────────────────────────────────────────────────────
# Shared base — duplicated verbatim across the 4 PyPI packages by design so
# each one stays self-contained (no cross-package imports). If you change the
# shape of _AegisGuard here, mirror the same change into
# aegis-anthropic / aegis-openai / aegis-bedrock so the four SDKs keep the
# same docstrings, defaults, and error surface.
# ─────────────────────────────────────────────────────────────────────────────


class AegisExecuteError(Exception):
    """Aegis /execute returned a non-decision response (5xx, non-JSON, etc.).

    The wrapper still surfaces a fail-closed `deny` decision to the caller —
    this exception is reserved for direct callers of `_call_execute` who want
    to distinguish transport failures from policy denials.
    """


class AegisNetworkError(AegisExecuteError):
    """Could not reach Aegis at all (DNS, TCP, TLS, timeout)."""


class _AegisGuard:
    """Private base class — owns the HTTP call into Aegis /execute.

    Each vendor-specific package re-implements this class verbatim so the
    PyPI wheels stay self-contained. The shape (constructor kwargs, method
    names, return contract) is identical across packages on purpose.

    The constructor follows the env-var fallback pattern every vendor
    wrapper already uses:
      - `aegis_key`  ← `AEGIS_API_KEY`
      - `aegis_url`  ← `AEGIS_URL`  (default `https://aegisagent.in`)
      - `tenant_id`  ← `AEGIS_TENANT_ID`
      - `agent_id`   ← `AEGIS_AGENT_ID`
    """

    _DEFAULT_AEGIS_URL = "https://aegisagent.in"
    _PACKAGE_NAME = "aegis-langchain"

    def __init__(
        self,
        *,
        aegis_key: str,
        aegis_url: str,
        tenant_id: str,
        agent_id: str,
        timeout: float = 8.0,
        max_retries: int = 3,
    ) -> None:
        self._url = (aegis_url or self._DEFAULT_AEGIS_URL).rstrip("/")
        self._agent_id = agent_id
        self._timeout = timeout
        self._max_retries = max(1, int(max_retries))
        self._headers = {
            "Authorization": f"Bearer {aegis_key}",
            "X-Tenant-ID": tenant_id,
            "X-Agent-ID": agent_id,
            "Content-Type": "application/json",
            "User-Agent": self._attach_user_agent_header(),
        }

    def _attach_user_agent_header(self) -> str:
        """Per-package User-Agent string sent on every /execute call."""
        return f"{self._PACKAGE_NAME}/{__version__}"

    def _call_execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST `payload` to /execute and return the parsed decision dict.

        Returns a dict shaped `{action, risk, findings, ...}`. NEVER raises
        on policy denials — instead returns a deny/escalate decision. Only
        raises `AegisNetworkError` / `AegisExecuteError` when even the
        fail-closed deny couldn't be synthesised (which in practice does
        not happen — the except clauses below catch everything).
        """
        import time as _time

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                with httpx.Client(timeout=self._timeout) as c:
                    resp = c.post(
                        f"{self._url}/execute",
                        headers=self._headers,
                        json=payload,
                    )
            except httpx.RequestError as exc:
                # Connect / read / write / timeout — retry with linear
                # backoff (0.1s, 0.2s, …). HTTP responses (4xx/5xx) are
                # NOT retried — they're deterministic and re-sending
                # won't change them in the short window the agent is
                # waiting.
                last_exc = exc
                if attempt < self._max_retries - 1:
                    _time.sleep(0.1 * (attempt + 1))
                continue

            if resp.status_code in (200, 403):
                # WAFv2 returns text/html on sensitive-path blocks;
                # resp.json() would raise JSONDecodeError. Surface as
                # `waf_blocked` so the agent sees a real reason.
                ctype = (resp.headers.get("content-type") or "").lower()
                if "html" in ctype or "json" not in ctype:
                    if resp.status_code == 403:
                        return {
                            "action":   "deny",
                            "risk":     1.0,
                            "findings": ["waf_blocked"],
                        }
                try:
                    return self._normalize(resp.json())
                except Exception:
                    if resp.status_code == 403:
                        return {
                            "action":   "deny",
                            "risk":     1.0,
                            "findings": ["waf_blocked"],
                        }
                    # Unparseable 200 — treat as fail-closed.
                    return {
                        "action":   "deny",
                        "risk":     1.0,
                        "findings": ["aegis_unparseable_response"],
                    }
            # 4xx other than 403, or 5xx — fail CLOSED. Letting unchecked
            # tool calls through because the security plane was unreachable
            # defeats the whole point of the integration.
            return {
                "action":   "deny",
                "risk":     1.0,
                "findings": [f"aegis_http_{resp.status_code}"],
            }

        # Exhausted retries on transport errors.
        return {
            "action":   "deny",
            "risk":     1.0,
            "findings": [f"aegis_error:{type(last_exc).__name__}"]
                        if last_exc else ["aegis_error:unknown"],
        }

    @staticmethod
    def _normalize(body: dict[str, Any]) -> dict[str, Any]:
        """Map every /execute response shape onto {action, risk, findings}.

        * 200 success → unwrap `data` envelope, return as-is.
        * 403 with `approval_required` → treat as `escalate`.
        * 403 with `error` set but `action` missing → treat as `deny` and
          carry the error string as a finding so the agent sees why.
        * Anything else with `success: false` → fail-closed deny.
        """
        data = body.get("data") if isinstance(body, dict) else None
        if data is None and isinstance(body, dict) and body.get("action"):
            data = body
        if isinstance(data, dict) and data.get("action"):
            return data
        err = (body.get("error") if isinstance(body, dict) else None) or "denied"
        action = "escalate" if "approval_required" in str(err).lower() else "deny"
        return {
            "action":   action,
            "risk":     1.0,
            "findings": [str(err)[:120]],
        }


class AegisClient(_AegisGuard):
    """Thin synchronous client for the Aegis /execute governance endpoint.

    Backwards-compatible adapter over `_AegisGuard`. Existing callers that
    construct `AegisClient(api_key=..., gateway_url=..., tenant_id=..., ...)`
    keep working unchanged.
    """

    def __init__(
        self,
        api_key: str,
        gateway_url: str = "https://aegisagent.in",
        tenant_id: str = "",
        agent_id: str = "langchain-agent",
        timeout: float = 10.0,
    ) -> None:
        super().__init__(
            aegis_key=api_key,
            aegis_url=gateway_url,
            tenant_id=tenant_id,
            agent_id=agent_id,
            timeout=timeout,
        )

    def check(self, tool_name: str, parameters: dict[str, Any]) -> dict[str, Any]:
        # Canonical field names — `tool_name`/`parameters` are accepted as
        # a fallback but the audit row gets logged with tool="unknown".
        # Use the right names.
        return self._call_execute({
            "agent_id":  self._agent_id,
            "tool":      tool_name,
            "arguments": parameters,
        })

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
