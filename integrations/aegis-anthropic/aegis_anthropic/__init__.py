"""
aegis-anthropic — Anthropic tool_use governance for Aegis ACP
Install: pip install aegis-anthropic  (or pip install -e integrations/aegis-anthropic)

Usage:
    from aegis_anthropic import AegisAnthropic

    client = AegisAnthropic(
        api_key="sk-ant-...",     # Anthropic key (or set ANTHROPIC_API_KEY)
        aegis_key="acp_...",      # Aegis API key (or set AEGIS_API_KEY)
        tenant_id="...",           # or set AEGIS_TENANT_ID
        agent_id="...",
    )

    # Use exactly like anthropic.Anthropic() — governance is automatic
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=1024,
        tools=[...],
        messages=[{"role": "user", "content": "Read /etc/passwd"}],
    )
    # tool_use blocks in the response are pre-checked;
    # blocked ones become text blocks explaining the denial.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

__version__ = "1.0.1"
__all__ = ["AegisAnthropic", "AegisClient", "__version__"]


class AegisClient:
    """Synchronous Aegis governance client (shared across all integrations)."""

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

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=self._timeout) as c:
                resp = c.post(
                    f"{self._url}/execute",
                    headers=self._headers,
                    json={
                        # Canonical field names the gateway expects. The
                        # `tool_name` / `parameters` aliases used to "work"
                        # via a fallback but the audit row was logged with
                        # tool="unknown" — a real governance bug.
                        "agent_id": self._agent_id,
                        "tool":      tool_name,
                        "arguments": tool_input,
                    },
                )
            if resp.status_code in (200, 403):
                # Sprint B follow-up 2026-06-14: the WAFv2 layer returns
                # `text/html` 403 for sensitive paths (e.g. /root/.aws/
                # credentials reads). Calling resp.json() on that raises
                # JSONDecodeError which surfaced as `aegis_error:…` and
                # confused buyers. Sniff Content-Type and synthesise the
                # waf_blocked finding so the agent sees a real reason.
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
                    # JSON parse fell over despite the Content-Type — still
                    # treat 403 as a WAF / edge block.
                    if resp.status_code == 403:
                        return {
                            "action":   "deny",
                            "risk":     1.0,
                            "findings": ["waf_blocked"],
                        }
                    raise
            # 4xx other than 403, or 5xx — treat as fail-CLOSED. Letting
            # unchecked tool calls through because the security plane was
            # unreachable defeats the whole point of the integration.
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
        # Error body, no decision payload — synthesize a safe one.
        err = (body.get("error") if isinstance(body, dict) else None) or "denied"
        action = "escalate" if "approval_required" in str(err).lower() else "deny"
        return {
            "action":   action,
            "risk":     1.0,
            "findings": [str(err)[:120]],
        }

    def is_blocked(self, decision: dict[str, Any]) -> bool:
        return decision.get("action", "deny") in (
            "deny", "block", "policy_deny", "reject", "escalate",
        )

    def blocked_text(self, tool_name: str, decision: dict[str, Any]) -> str:
        findings = decision.get("findings", ["policy_violation"])
        risk = decision.get("risk", 1.0)
        return (
            f"[BLOCKED by Aegis] Tool '{tool_name}' was denied before execution "
            f"(risk={risk:.3f}, findings={findings}). "
            "Adjust your approach or contact your administrator."
        )


class _GovernedMessages:
    """Wraps anthropic.messages to intercept tool_use blocks."""

    def __init__(self, messages: Any, aegis: AegisClient) -> None:
        self._messages = messages
        self._aegis = aegis

    def create(self, **kwargs: Any) -> Any:
        response = self._messages.create(**kwargs)
        return self._govern_response(response)

    def _govern_response(self, response: Any) -> Any:
        """
        Walk response.content looking for tool_use blocks.
        Blocked ones are replaced with text blocks explaining the denial.
        Allowed ones pass through unchanged.
        """
        try:
            import anthropic as _anthropic

            new_content = []
            for block in response.content:
                if block.type != "tool_use":
                    new_content.append(block)
                    continue

                decision = self._aegis.check(block.name, block.input)
                if self._aegis.is_blocked(decision):
                    # Replace tool_use with a text explanation
                    new_content.append(
                        _anthropic.types.TextBlock(
                            type="text",
                            text=self._aegis.blocked_text(block.name, decision),
                        )
                    )
                    # Track block metadata on the response object
                    if not hasattr(response, "_aegis_blocked"):
                        response._aegis_blocked = []
                    response._aegis_blocked.append({
                        "tool_use_id": block.id,
                        "tool_name": block.name,
                        "decision": decision,
                    })
                else:
                    new_content.append(block)

            response.content = new_content
        except Exception:
            pass  # never break the caller
        return response


class AegisAnthropic:
    """
    Drop-in replacement for anthropic.Anthropic().
    Intercepts every tool_use block and checks it with Aegis before execution.

    The caller's tool-execution loop works unchanged — blocked tools simply
    never appear in tool_calls, replaced by explanatory text blocks.
    """

    def __init__(
        self,
        api_key: str | None = None,
        aegis_key: str | None = None,
        gateway_url: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        timeout: float = 10.0,
        **anthropic_kwargs: Any,
    ) -> None:
        try:
            import anthropic as _anthropic
            self._claude = _anthropic.Anthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
                **anthropic_kwargs,
            )
        except ImportError as exc:
            raise ImportError("pip install anthropic") from exc

        self._aegis = AegisClient(
            api_key=aegis_key or os.environ["AEGIS_API_KEY"],
            gateway_url=gateway_url or os.environ.get("AEGIS_URL", "https://aegisagent.in"),
            tenant_id=tenant_id or os.environ.get("AEGIS_TENANT_ID", ""),
            agent_id=agent_id or os.environ.get("AEGIS_AGENT_ID", "anthropic-agent"),
            timeout=timeout,
        )
        self.messages = _GovernedMessages(self._claude.messages, self._aegis)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._claude, name)
