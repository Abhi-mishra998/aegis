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

__version__ = "1.1.3"
__all__ = [
    "AegisOpenAI",
    "AegisOpenAIProxy",
    "AegisApprovalPending",
    "AegisApprovalRejected",
    "AegisApprovalTimeout",
    "AegisClient",
    "AegisExecuteError",
    "AegisNetworkError",
    "__version__",
]


# ─────────────────────────────────────────────────────────────────────────────
# Shared base — duplicated verbatim across the 4 PyPI packages by design so
# each one stays self-contained (no cross-package imports). If you change the
# shape of _AegisGuard here, mirror the same change into
# aegis-anthropic / aegis-langchain / aegis-bedrock so the four SDKs keep the
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
    _PACKAGE_NAME = "aegis-openai"

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
            # QA-SDK-FIX (2026-06-24) — see aegis-anthropic for rationale.
            # Surface 429 as ``action=rate_limited`` (infra throttle), not
            # ``deny`` (policy violation). Mirrors the canonical fix across
            # all 4 wrapper packages.
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After") or ""
                findings = ["aegis_rate_limited"]
                if retry_after:
                    findings.append(f"retry_after={retry_after}")
                return {
                    "action":   "rate_limited",
                    "risk":     0.0,
                    "findings": findings,
                }
            # 4xx other than 403/429, or 5xx — fail CLOSED. Letting unchecked
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
    """Synchronous Aegis governance client.

    Backwards-compatible adapter over `_AegisGuard`. Existing callers that
    construct `AegisClient(api_key=..., gateway_url=..., tenant_id=..., ...)`
    keep working unchanged.
    """

    def __init__(
        self,
        api_key: str,
        gateway_url: str,
        tenant_id: str,
        agent_id: str,
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
        aegis_url: str | None = None,  # deprecated alias
        api_key: str | None = None,    # alias for openai_api_key — see note below
        **openai_kwargs: Any,
    ) -> None:
        # QA-SDK-FIX (2026-06-24) — canonical SDK kwargs across the 4
        # wrapper packages: ``api_key`` (the LLM provider key) +
        # ``aegis_key`` + ``gateway_url`` + ``tenant_id`` + ``agent_id``.
        # The OpenAI wrapper historically named its provider-key kwarg
        # ``openai_api_key`` to avoid confusion with anthropic, which
        # made cross-LLM portable code awkward. Both names are now
        # accepted; ``openai_api_key`` is the preferred form for code
        # that imports only this package. Same deprecation alias for
        # ``aegis_url=`` as on the other wrappers.
        if openai_api_key is None and api_key is not None:
            openai_api_key = api_key
        if gateway_url is None and aegis_url is not None:
            import warnings as _warnings
            _warnings.warn(
                "AegisOpenAI: `aegis_url=` is deprecated; use `gateway_url=` "
                "to match the canonical Aegis SDK kwarg.",
                DeprecationWarning,
                stacklevel=2,
            )
            gateway_url = aegis_url
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
