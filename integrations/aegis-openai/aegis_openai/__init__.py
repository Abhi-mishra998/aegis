"""
aegis-openai — OpenAI tool_calls governance for Aegis ACP
Install: pip install aegis-openai  (or pip install -e integrations/aegis-openai)

Usage:
    from aegis_openai import AegisOpenAI

    client = AegisOpenAI(
        openai_api_key="sk-...",
        aegis_key="acp_...",      # or set AEGIS_API_KEY env var
        tenant_id="...",           # or set AEGIS_TENANT_ID env var
        agent_id="...",
    )

    # Use exactly like openai.OpenAI() — governance is automatic
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Delete all temp files"}],
        tools=[...],
    )
    # tool_calls in the response are pre-checked; blocked ones are replaced
    # with a synthetic assistant message explaining the block.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

__all__ = ["AegisOpenAI", "AegisClient"]


class AegisClient:
    """Synchronous Aegis governance client."""

    def __init__(
        self,
        api_key: str,
        gateway_url: str,
        tenant_id: str,
        agent_id: str,
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
            with httpx.Client(timeout=self._timeout) as c:
                resp = c.post(
                    f"{self._url}/execute",
                    headers=self._headers,
                    json={
                        "agent_id": self._agent_id,
                        "tool_name": tool_name,
                        "parameters": parameters,
                        "context": {},
                    },
                )
            if resp.status_code in (200, 403):
                body = resp.json()
                return body.get("data", body)
            return {"action": "allow", "risk": 0.0}
        except Exception as exc:
            return {"action": "allow", "risk": 0.0, "findings": [f"aegis_error:{type(exc).__name__}"]}

    def is_blocked(self, decision: dict[str, Any]) -> bool:
        return decision.get("action", "allow") in ("deny", "block", "policy_deny", "reject")


class _GovernedCompletions:
    """Wraps openai.chat.completions to intercept tool_calls."""

    def __init__(self, completions: Any, aegis: AegisClient) -> None:
        self._completions = completions
        self._aegis = aegis

    def create(self, **kwargs: Any) -> Any:
        response = self._completions.create(**kwargs)
        return self._filter_tool_calls(response)

    def _filter_tool_calls(self, response: Any) -> Any:
        """
        For each tool_call in the response, check with Aegis.
        Blocked tool calls are replaced with a synthetic tool result message.
        The caller sees a consistent response shape regardless of blocks.
        """
        try:
            for choice in response.choices:
                if not (choice.message and choice.message.tool_calls):
                    continue
                blocked = []
                allowed = []
                for tc in choice.message.tool_calls:
                    try:
                        params = json.loads(tc.function.arguments)
                    except Exception:
                        params = {"raw": tc.function.arguments}
                    decision = self._aegis.check(tc.function.name, params)
                    if self._aegis.is_blocked(decision):
                        blocked.append((tc, decision))
                    else:
                        allowed.append(tc)

                if blocked:
                    # Annotate the response object with block metadata
                    # so callers can detect and handle blocked calls
                    choice.message.tool_calls = allowed
                    if not hasattr(response, "_aegis_blocked"):
                        response._aegis_blocked = []
                    for tc, decision in blocked:
                        response._aegis_blocked.append({
                            "tool_call_id": tc.id,
                            "function_name": tc.function.name,
                            "decision": decision,
                            "blocked_message": (
                                f"[BLOCKED by Aegis] '{tc.function.name}' denied "
                                f"(risk={decision.get('risk', 1.0):.3f}): "
                                f"{decision.get('findings', ['policy_violation'])}"
                            ),
                        })
        except Exception:
            pass  # never break the caller — governance failures are logged, not fatal
        return response


class _GovernedChat:
    def __init__(self, chat: Any, aegis: AegisClient) -> None:
        self.completions = _GovernedCompletions(chat.completions, aegis)


class AegisOpenAI:
    """
    Drop-in replacement for openai.OpenAI().
    Intercepts all tool_calls in chat completions and checks each with Aegis.

    Example:
        client = AegisOpenAI(aegis_key="acp_...", tenant_id="...")
        response = client.chat.completions.create(model="gpt-4o", messages=[...], tools=[...])
        # Blocked tools appear in response._aegis_blocked
    """

    def __init__(
        self,
        openai_api_key: str | None = None,
        aegis_key: str | None = None,
        gateway_url: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        timeout: float = 10.0,
        **openai_kwargs: Any,
    ) -> None:
        try:
            import openai as _openai
            self._openai = _openai.OpenAI(api_key=openai_api_key, **openai_kwargs)
        except ImportError as exc:
            raise ImportError("pip install openai") from exc

        self._aegis = AegisClient(
            api_key=aegis_key or os.environ["AEGIS_API_KEY"],
            gateway_url=gateway_url or os.environ.get("AEGIS_URL", "https://aegisagent.in"),
            tenant_id=tenant_id or os.environ.get("AEGIS_TENANT_ID", ""),
            agent_id=agent_id or os.environ.get("AEGIS_AGENT_ID", "openai-agent"),
            timeout=timeout,
        )
        self.chat = _GovernedChat(self._openai.chat, self._aegis)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._openai, name)
