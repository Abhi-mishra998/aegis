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

__version__ = "1.1.4"
__all__ = [
    "AegisAnthropic",
    "AegisAnthropicProxy",
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
# aegis-openai / aegis-langchain / aegis-bedrock so the four SDKs keep the
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
    _PACKAGE_NAME = "aegis-anthropic"

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
        # arch-26 W4.3 2026-06-26 — reuse one httpx.Client per SDK instance
        # so we get TCP/TLS connection pooling. The previous per-call
        # `with httpx.Client(...) as c:` pattern opened and closed a fresh
        # connection on every /execute, adding ~50–150ms of TLS handshake
        # latency on the hot path. The Client is closed in close()/__del__.
        self._http: httpx.Client = httpx.Client(timeout=self._timeout)

    def close(self) -> None:
        """Release the underlying httpx connection pool.

        Safe to call multiple times. Called automatically on __del__ as a
        backstop, but explicit close() is preferred so the pool releases
        deterministically (Python __del__ ordering is interpreter-dependent
        and may run after the event loop is gone in async contexts).
        """
        h = getattr(self, "_http", None)
        if h is not None:
            try:
                h.close()
            except Exception:
                pass
            self._http = None  # type: ignore[assignment]

    def __del__(self) -> None:  # noqa: D401 — destructor
        # Best-effort cleanup; safe to no-op if attributes missing
        # (e.g. __init__ raised partway through).
        try:
            self.close()
        except Exception:
            pass

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
                # arch-26 W4.3 — reuse self._http instead of opening a
                # new Client per call. ~50-150ms saved on the hot path
                # via TCP/TLS connection pooling.
                resp = self._http.post(
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
            # QA-SDK-FIX (2026-06-24) — 429 is rate-limit / capacity
            # back-pressure, NOT a security decision. The previous behaviour
            # routed it through the same fail-closed branch as 4xx/5xx and
            # returned ``action=deny, risk=1.0, findings=[aegis_http_429]``
            # — the agent then saw a high-risk policy denial in its
            # tool-result text, polluting telemetry that branches on
            # "deny rate". Surface 429 as its own action so callers can
            # distinguish "infrastructure throttle, retry shortly" from
            # "policy denied this tool call". ``is_blocked`` still returns
            # True (we don't let the unaudited tool through), but
            # ``blocked_text`` emits a retry-friendly message instead of
            # the "[BLOCKED by Aegis]" string.
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
    """Synchronous Aegis governance client (shared across all integrations).

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

    def check(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        # Canonical field names the gateway expects. The
        # `tool_name` / `parameters` aliases used to "work" via a fallback
        # but the audit row was logged with tool="unknown" — a real
        # governance bug.
        return self._call_execute({
            "agent_id":  self._agent_id,
            "tool":      tool_name,
            "arguments": tool_input,
        })

    def is_blocked(self, decision: dict[str, Any]) -> bool:
        # QA-SDK-FIX (2026-06-24) — "rate_limited" is also blocking-from-
        # the-agent's-perspective (we don't let the tool through), but
        # downstream telemetry must be able to tell it apart from a real
        # policy deny. ``blocked_text`` emits a different message; this
        # method only answers the boolean "should I skip the tool call?".
        return decision.get("action", "deny") in (
            "deny", "block", "policy_deny", "reject", "escalate", "rate_limited",
        )

    def blocked_text(self, tool_name: str, decision: dict[str, Any]) -> str:
        findings = decision.get("findings", ["policy_violation"])
        risk = decision.get("risk", 1.0)
        # QA-SDK-FIX (2026-06-24) — distinguish infra throttle from policy
        # denial in the agent-visible message. Conflating the two led the
        # LLM to reason as if its tool call was a security violation when
        # it was just a 429 from a busy gateway.
        if decision.get("action") == "rate_limited":
            retry_hint = ""
            for f in findings:
                if isinstance(f, str) and f.startswith("retry_after="):
                    retry_hint = f" Retry-After: {f.split('=', 1)[1]}s."
                    break
            return (
                f"[Aegis rate-limited] The Aegis gateway responded HTTP 429 for "
                f"tool '{tool_name}'. This is a transient capacity signal, NOT a "
                f"policy denial.{retry_hint} Retry shortly; do not adjust the "
                f"request shape."
            )
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
        aegis_url: str | None = None,  # deprecated alias
        **anthropic_kwargs: Any,
    ) -> None:
        # QA-SDK-FIX (2026-06-24) — accept the deprecated ``aegis_url`` as
        # an alias for ``gateway_url``. Earlier docs (setup-agies.md prior
        # to the 2026-06-24 update) and the tester-prompt both used the old
        # name; customers copy-pasting from those got a TypeError on
        # construct. Emit a DeprecationWarning so authors update their
        # call-sites without immediately breaking them.
        if gateway_url is None and aegis_url is not None:
            import warnings as _warnings
            _warnings.warn(
                "AegisAnthropic: `aegis_url=` is deprecated; use `gateway_url=` "
                "to match the canonical Aegis SDK kwarg.",
                DeprecationWarning,
                stacklevel=2,
            )
            gateway_url = aegis_url
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


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 20 — Path B (Anthropic-compatible /v1/messages proxy).
#
# The Path A wrapper above sits in the agent process and gates tool calls
# locally. Path B sits in front of Anthropic itself: the developer just
# replaces base_url with https://aegisagent.in/v1 and Aegis becomes
# the gateway. Today Aegis returns HTTP 202 with a pending_approval body
# when a prompt matches an escalation pattern (wire transfer, k8s prod
# destruction, etc.). The official Anthropic SDK doesn't know about 202
# — it'll raise an error. So this wrapper handles the polling + replay
# transparently:
#
#   client = AegisAnthropicProxy(employee_key="acp_emp_…")
#   resp = client.messages.create(model="claude-haiku-4-5", ...)
#
# If the prompt is safe → 1 HTTP round-trip, normal response.
# If the prompt escalates → wrapper polls /approvals/{id}/status with
# backoff, then on approve replays with X-Aegis-Approval-ID and returns
# the final 200. On reject → AegisApprovalRejected. On timeout →
# AegisApprovalTimeout.
#
# The wrapper is intentionally tiny: it speaks raw HTTP rather than the
# Anthropic SDK's RPC stack so a customer can use it without the
# anthropic package installed. If they DO want the SDK's pydantic
# types, set return_raw=False and we hand back a Message instance.
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
    """The operator denied the escalation. Look at .reason for the operator's note."""
    def __init__(self, approval_id: str, reason: str | None) -> None:
        super().__init__(f"Aegis rejected approval {approval_id}: {reason or '<no reason>'}")
        self.approval_id = approval_id
        self.reason = reason


class AegisApprovalTimeout(Exception):
    """The poll loop hit the deadline before the approval resolved."""
    def __init__(self, approval_id: str, waited_s: float) -> None:
        super().__init__(
            f"Aegis approval {approval_id} not resolved after {waited_s:.1f}s — still pending",
        )
        self.approval_id = approval_id
        self.waited_s = waited_s


class _ProxyMessages:
    """Anthropic-compatible messages.create on top of /v1/messages."""

    def __init__(self, parent: "AegisAnthropicProxy") -> None:
        self._p = parent

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, Any]],
        approval_id: str | None = None,
        poll_until_decided: bool = True,
        approval_timeout_s: float | None = None,
        approval_poll_interval_s: float | None = None,
        **extra: Any,
    ) -> Any:
        """Create a message. Returns the Anthropic response body (dict).

        On escalation:
          - poll_until_decided=True  (default): block until the operator
            approves or rejects, then return the final Anthropic body
            (or raise AegisApprovalRejected / AegisApprovalTimeout).
          - poll_until_decided=False: raise AegisApprovalPending so the
            caller can integrate with their own queue / webhook.

        Pass approval_id explicitly to short-circuit a previously
        approved replay (useful when your code remembered the id and
        wants to re-fire without waiting for the operator round-trip).
        """
        body: dict[str, Any] = {
            "model":      model,
            "max_tokens": max_tokens,
            "messages":   messages,
        }
        body.update({k: v for k, v in extra.items() if v is not None})

        headers = {
            "x-api-key":         self._p._employee_key,
            "anthropic-version": self._p._anthropic_version,
            "Content-Type":      "application/json",
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
                resp = c.post(self._p._messages_url, headers=headers, json=body)
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
                    # Poll, then replay.
                    self._p._poll_until_decided(c, aid, deadline, interval)
                    headers["X-Aegis-Approval-ID"] = aid
                    continue

                if resp.status_code == 403:
                    detail = resp.json()
                    # Two flavors: prompt_blocked (deny-path) and
                    # approval_rejected (operator denial on replay).
                    err = ((detail.get("detail") if isinstance(detail.get("detail"), dict)
                            else None) or detail)
                    err_code = err.get("error") if isinstance(err, dict) else None
                    if err_code == "approval_rejected":
                        raise AegisApprovalRejected(
                            approval_id=approval_id or "",
                            reason=(err.get("reason") if isinstance(err, dict) else None),
                        )
                    # Otherwise it's a hard policy block — bubble up so
                    # the caller sees the same Anthropic-like error.
                    resp.raise_for_status()

                resp.raise_for_status()


class AegisAnthropicProxy:
    """Path B wrapper — Aegis-fronted Anthropic /v1/messages.

    Constructor:
        client = AegisAnthropicProxy(
            employee_key="acp_emp_…",                       # or set AEGIS_EMPLOYEE_KEY
            gateway_url="https://aegisagent.in",         # or AEGIS_URL
            anthropic_version="2023-06-01",
            timeout_s=60.0,
            approval_timeout_s=300.0,
            approval_poll_interval_s=3.0,
        )

    Usage:
        resp = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": "…"}],
        )

    The 202 → poll → replay loop is transparent. If you want to handle
    pending approvals yourself, pass poll_until_decided=False.
    """

    def __init__(
        self,
        employee_key: str | None = None,
        gateway_url: str | None = None,
        anthropic_version: str = "2023-06-01",
        timeout_s: float = 60.0,
        approval_timeout_s: float = 300.0,
        approval_poll_interval_s: float = 3.0,
    ) -> None:
        ek = employee_key or os.environ.get("AEGIS_EMPLOYEE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
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
        self._messages_url = f"{base}/v1/messages"
        # /v1/approvals is in the gateway middleware skip-list so the
        # SDK's x-api-key auth works without a Bearer JWT. The
        # /v1/* alias strips the prefix before routing, so the same
        # handler (dual-auth) serves both /v1/approvals/* (SDK) and
        # /approvals/* (operator inbox).
        self._approvals_base = f"{base}/v1/approvals"
        self._anthropic_version = anthropic_version
        self._timeout_s = timeout_s
        self._approval_timeout_s = approval_timeout_s
        self._approval_poll_interval_s = approval_poll_interval_s
        self.messages = _ProxyMessages(self)

    def get_approval_status(self, approval_id: str) -> dict[str, Any]:
        """Read the current state of an approval. Returns the data envelope."""
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
            # pending — sleep, check deadline.
            if deadline is not None and _time.monotonic() >= deadline:
                waited = self._approval_timeout_s
                raise AegisApprovalTimeout(approval_id, waited)
            _time.sleep(interval)
