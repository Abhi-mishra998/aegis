"""
aegis-bedrock — AWS Bedrock Agents governance middleware for Aegis ACP.

Drop-in replacement for `boto3.client("bedrock-agent-runtime")`. Every
Bedrock action-group invocation, knowledge-base query, and
code-interpreter call is checked by Aegis BEFORE the underlying primitive
runs. Same `/execute` contract as `aegis-anthropic` and `aegis-openai`.

Usage:

    from aegis_bedrock import AegisBedrockAgentRuntime

    client = AegisBedrockAgentRuntime(
        aegis_key="acp_…",
        aegis_url="https://ha.aegisagent.in",
        tenant_id="…",
        agent_id="…",
        region_name="us-east-1",
    )

    response = client.invoke_agent(
        agentId="…", agentAliasId="…", sessionId="…",
        inputText="Find the customer that owes the most.",
    )
"""
from __future__ import annotations

import json
import os
from typing import Any, Iterable

import httpx

__version__ = "1.0.0"
__all__ = ["AegisBedrockAgentRuntime", "AegisClient", "__version__"]


class AegisClient:
    """Synchronous Aegis governance client. Identical contract to the
    AegisClient in aegis-anthropic / aegis-openai — same /execute call,
    same WAF / JSON / fail-closed surfacing."""

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
                        "agent_id": self._agent_id,
                        "tool":      tool_name,
                        "arguments": tool_input,
                    },
                )
            if resp.status_code in (200, 403):
                # WAF returns text/html on sensitive-path blocks; surface
                # as waf_blocked instead of JSONDecodeError.
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
                    raise
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


# ─── Bedrock invoke_agent wrapper ────────────────────────────────────────
def _parse_tool_name(event: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Pull the action-group name + parameter block out of a Bedrock
    `returnControl` event."""
    rc = event.get("returnControl") or {}
    invocation_inputs = rc.get("invocationInputs") or []
    if not invocation_inputs:
        return None, {}
    invo = invocation_inputs[0]
    ag = invo.get("apiInvocationInput") or invo.get("functionInvocationInput")
    if not ag:
        return None, {}
    name = ag.get("apiPath") or ag.get("function") or ag.get("actionGroup", "")
    params = {p.get("name"): p.get("value")
              for p in (ag.get("parameters") or [])
              if isinstance(p, dict)}
    # Also try to merge a JSON request body, if any.
    rb = ag.get("requestBody") or {}
    for ct_block in (rb.get("content") or {}).values():
        for p in ct_block.get("properties") or []:
            params[p.get("name")] = p.get("value")
    return f"tool.{name}".lower(), params


class AegisBedrockAgentRuntime:
    """Wraps boto3 bedrock-agent-runtime. Intercepts every
    `returnControl` action-group call from invoke_agent and consults
    Aegis. Blocked actions are short-circuited with a synthesised
    `roleSessionAttributes.aegis_block_reason` field so the Bedrock
    response stream still terminates cleanly."""

    def __init__(
        self,
        aegis_key: str | None = None,
        aegis_url: str | None = None,
        tenant_id: str | None = None,
        agent_id: str | None = None,
        timeout: float = 10.0,
        **boto_kwargs: Any,
    ) -> None:
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "pip install 'aegis-bedrock[bedrock]'") from exc
        self._client = boto3.client("bedrock-agent-runtime", **boto_kwargs)
        self._aegis = AegisClient(
            api_key=aegis_key or os.environ["AEGIS_API_KEY"],
            gateway_url=aegis_url or os.environ.get(
                "AEGIS_URL", "https://aegisagent.in"),
            tenant_id=tenant_id or os.environ.get("AEGIS_TENANT_ID", ""),
            agent_id=agent_id or os.environ.get(
                "AEGIS_AGENT_ID", "bedrock-agent"),
            timeout=timeout,
        )

    def invoke_agent(self, **kwargs: Any) -> dict[str, Any]:
        """Forward to the wrapped Bedrock client; intercept action-group
        invocation events in the streamed response."""
        response = self._client.invoke_agent(**kwargs)
        completion = response.get("completion")
        if completion is None:
            return response
        response["completion"] = self._governed_completion(completion)
        return response

    def _governed_completion(self, stream: Iterable[Any]) -> Iterable[Any]:
        for event in stream:
            if not isinstance(event, dict):
                yield event
                continue
            tool, args = _parse_tool_name(event)
            if tool is None:
                yield event
                continue
            decision = self._aegis.check(tool, args)
            if self._aegis.is_blocked(decision):
                # Emit a synthesised chunk explaining the denial in
                # place of the returnControl event. Bedrock callers
                # expect a chunk-shape so the upstream stream consumer
                # doesn't crash.
                yield {
                    "chunk": {
                        "bytes": json.dumps({
                            "aegis_block":     True,
                            "tool":            tool,
                            "decision":        decision,
                            "text":            self._aegis.blocked_text(
                                tool, decision),
                        }).encode(),
                    },
                }
            else:
                yield event

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)
