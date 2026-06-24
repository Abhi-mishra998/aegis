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

__version__ = "1.1.1"
__all__ = [
    "AegisOpenAI",
    "AegisOpenAIProxy",
    "AegisApprovalPending",
    "AegisApprovalRejected",
    "AegisApprovalTimeout",
    "AegisClient",
    "__version__",
]


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
                # sensitive-path blocks; resp.json() would raise. Synthesise
                # waf_blocked so the buyer sees a real reason, not a parse
                # error.
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
            # Fail CLOSED on transport / server errors. Letting unchecked
            # tool calls through because Aegis was unreachable defeats the
            # purpose of installing this package.
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


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 22 — Path B (OpenAI-compatible /v1/chat/completions proxy).
#
# Mirror of AegisAnthropicProxy: the developer hits .chat.completions.create(...)
# and gets the final 200 body back. If Aegis returns 202, the wrapper polls
# /v1/approvals/{id}/status and replays with X-Aegis-Approval-ID once approved.
# Rejection raises AegisApprovalRejected; timeout raises AegisApprovalTimeout.
# ─────────────────────────────────────────────────────────────────────────────


class AegisApprovalPending(Exception):
    """Raised only when poll_until_decided=False and the loop returns mid-flight."""
    def __init__(self, approval_id: str, approver_role: str, matched_pattern: str) -> None:
        super().__init__(
            f"Aegis is awaiting {approver_role} approval (approval_id={approval_id}, "
            f"matched_pattern={matched_pattern})",
        )
        self.approval_id = approval_id
        self.approver_role = approver_role
        self.matched_pattern = matched_pattern


class AegisApprovalRejected(Exception):
    def __init__(self, approval_id: str, reason: str | None) -> None:
        super().__init__(f"Aegis rejected approval {approval_id}: {reason or '<no reason>'}")
        self.approval_id = approval_id
        self.reason = reason


class AegisApprovalTimeout(Exception):
    def __init__(self, approval_id: str, waited_s: float) -> None:
        super().__init__(
            f"Aegis approval {approval_id} not resolved after {waited_s:.1f}s — still pending",
        )
        self.approval_id = approval_id
        self.waited_s = waited_s


class _ProxyChatCompletions:
    def __init__(self, parent: "AegisOpenAIProxy") -> None:
        self._p = parent

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        approval_id: str | None = None,
        poll_until_decided: bool = True,
        approval_timeout_s: float | None = None,
        approval_poll_interval_s: float | None = None,
        **extra: Any,
    ) -> Any:
        body: dict[str, Any] = {"model": model, "messages": messages}
        body.update({k: v for k, v in extra.items() if v is not None})

        # OpenAI SDK auth convention is Authorization: Bearer …; the
        # Aegis proxy also accepts x-api-key for parity. We send both
        # so the wrapper still works if the customer fronts another
        # proxy that strips Authorization headers.
        headers = {
            "Authorization": f"Bearer {self._p._employee_key}",
            "x-api-key":     self._p._employee_key,
            "Content-Type":  "application/json",
        }
        if approval_id:
            headers["X-Aegis-Approval-ID"] = approval_id

        import time as _time
        deadline = (
            _time.monotonic() + (approval_timeout_s or self._p._approval_timeout_s)
            if poll_until_decided else None
        )
        interval = approval_poll_interval_s or self._p._approval_poll_interval_s

        with httpx.Client(timeout=self._p._timeout_s) as c:
            while True:
                resp = c.post(self._p._chat_url, headers=headers, json=body)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 202:
                    payload = resp.json()
                    aid = payload.get("approval_id") or approval_id or ""
                    if not poll_until_decided:
                        raise AegisApprovalPending(
                            approval_id=aid,
                            approver_role=payload.get("approver_role") or "",
                            matched_pattern=payload.get("matched_pattern") or "",
                        )
                    self._p._poll_until_decided(c, aid, deadline, interval)
                    headers["X-Aegis-Approval-ID"] = aid
                    continue
                if resp.status_code == 403:
                    detail = resp.json()
                    err = ((detail.get("detail") if isinstance(detail.get("detail"), dict)
                            else None) or detail)
                    err_code = err.get("error") if isinstance(err, dict) else None
                    if err_code == "approval_rejected":
                        raise AegisApprovalRejected(
                            approval_id=approval_id or "",
                            reason=(err.get("reason") if isinstance(err, dict) else None),
                        )
                    resp.raise_for_status()
                resp.raise_for_status()


class _ProxyChat:
    """Mirrors the openai.OpenAI().chat namespace."""
    def __init__(self, parent: "AegisOpenAIProxy") -> None:
        self.completions = _ProxyChatCompletions(parent)


class AegisOpenAIProxy:
    """Path B wrapper — Aegis-fronted OpenAI /v1/chat/completions.

    Constructor:
        client = AegisOpenAIProxy(
            employee_key="acp_emp_…",                       # or AEGIS_EMPLOYEE_KEY
            gateway_url="https://aegisagent.in",         # or AEGIS_URL
            timeout_s=60.0,
            approval_timeout_s=300.0,
            approval_poll_interval_s=3.0,
        )

    Usage:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "…"}],
        )

    The 202 → poll → replay loop is transparent. Pass
    poll_until_decided=False to handle pending approvals yourself.
    """
    def __init__(
        self,
        employee_key: str | None = None,
        gateway_url: str | None = None,
        timeout_s: float = 60.0,
        approval_timeout_s: float = 300.0,
        approval_poll_interval_s: float = 3.0,
    ) -> None:
        ek = (
            employee_key
            or os.environ.get("AEGIS_EMPLOYEE_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
        if not ek or not ek.startswith("acp_emp_"):
            raise ValueError(
                "employee_key must be an Aegis virtual key (acp_emp_…). "
                "Mint one in the Aegis Team page → Add employee.",
            )
        self._employee_key = ek
        base = (
            gateway_url
            or os.environ.get("AEGIS_URL")
            or "https://aegisagent.in"
        ).rstrip("/")
        self._chat_url = f"{base}/v1/chat/completions"
        # /v1/approvals is skip-listed at the gateway middleware so the
        # x-api-key auth works without a Clerk JWT.
        self._approvals_base = f"{base}/v1/approvals"
        self._timeout_s = timeout_s
        self._approval_timeout_s = approval_timeout_s
        self._approval_poll_interval_s = approval_poll_interval_s
        self.chat = _ProxyChat(self)

    def get_approval_status(self, approval_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=self._timeout_s) as c:
            resp = c.get(
                f"{self._approvals_base}/{approval_id}/status",
                headers={"x-api-key": self._employee_key},
            )
            resp.raise_for_status()
            body = resp.json() or {}
            return body.get("data") or {}

    def _poll_until_decided(
        self,
        client: httpx.Client,
        approval_id: str,
        deadline: float | None,
        interval: float,
    ) -> None:
        import time as _time
        while True:
            status = client.get(
                f"{self._approvals_base}/{approval_id}/status",
                headers={"x-api-key": self._employee_key},
            )
            status.raise_for_status()
            data = (status.json() or {}).get("data") or {}
            state = (data.get("status") or "").lower()
            if state == "approved":
                return
            if state == "rejected":
                raise AegisApprovalRejected(
                    approval_id=approval_id,
                    reason=data.get("reason"),
                )
            if deadline is not None and _time.monotonic() >= deadline:
                raise AegisApprovalTimeout(
                    approval_id, self._approval_timeout_s,
                )
            _time.sleep(interval)
