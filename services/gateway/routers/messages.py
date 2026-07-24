"""Sprint 17 — Aegis for Teams: Anthropic-compatible /v1/messages proxy.

The user pattern this endpoint serves:

    Company has a corporate Anthropic API key.
    Company has 10 employees who want to use Claude in their day job.
    Company doesn't want to give the raw corporate key to all 10.

Aegis answer: the company admin mints one ``acp_emp_…`` virtual key
PER EMPLOYEE in /api-keys/employees. Each employee replaces their
local ``ANTHROPIC_API_KEY`` with the virtual key and points the
official Anthropic SDK at ``https://ha.aegisagent.in`` instead of
``api.anthropic.com``. From the SDK's point of view nothing changed.
From Aegis's point of view:

  - every message is attributed to ``subject_email`` for the per-team
    spend dashboard (Sprint 17.3)
  - daily / monthly budget caps refuse the request BEFORE the corporate
    Anthropic key is touched
  - the existing Aegis signal registry (Sprint 7) will run on the
    prompt body in a follow-on round so harmful prompts get blocked
  - usage is metered in Redis + audit-rowed for the Merkle chain

This module deliberately stays Anthropic-compatible: same path
(``/v1/messages``), same headers (``x-api-key``, ``anthropic-version``),
same request + response schema. The SDK swap is one env var on the
employee's machine.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from sdk.common.atf_class import classify as _atf_classify
from sdk.common.audit_stream import push_audit_event
from sdk.common.background import swallow_log
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway import escalation_patterns, proxy_helpers, slack_approvals
from services.gateway._helpers import internal_headers, publish_event
from services.gateway.anthropic_pricing import cost_usd
from services.gateway.client import service_client
from services.gateway.inference_proxy import InjectionDetector
from services.policy import packs as policy_packs

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["llm-proxy"])

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION_DEFAULT = "2023-06-01"
_UPSTREAM_TIMEOUT_S = 60.0
# Sprint 21 — the public-facing base URL the Slack callback links point
# at. Comes from settings.PUBLIC_BASE_URL when set, otherwise falls back
# to the well-known prod-ha hostname.
_PUBLIC_BASE_URL = getattr(settings, "PUBLIC_BASE_URL", "") or "https://ha.aegisagent.in"


# Per-employee budget bookkeeping, approval lookup, Slack + policy-
# pack fetch all live in services/gateway/proxy_helpers.py so the
# Anthropic + OpenAI proxies don't duplicate them. Extracted in the
# 2026-06-17 dead-code audit — see SPRINT.md ledger.


# ─────────────────────────────────────────────────────────────────────
# /v1/messages — Anthropic-compatible. The SDK behaves as if it's
# talking to api.anthropic.com directly. We just sit in front of it.
# ─────────────────────────────────────────────────────────────────────


import re as _re

# ATF §3.3 pre-classifier tokens — distinctly-destructive verbs. The
# match is TOKEN-boundary (separators: `_`, `-`, `.`, `/`, digits) so
# `send_payment` matches `pay` (kept in list only after read-verb
# exemption below fires) — see the exemption logic for the whole
# story. Ambiguous tokens (`pay`, `wire`, `transfer`) are ALSO in this
# default list because they most-often appear in destructive tool
# names, BUT any tool whose name begins with a read-verb prefix
# (`get_`, `list_`, `check_`, `describe_`, `show_`, `read_`, `fetch_`,
# `is_`, `has_`, `count_`, `undelete_`, `restore_`, `recover_`) is
# treated as C2 regardless — a `get_pay_history` is a read even
# though `pay` is in the token list. Tenants extend via
# `ACP_C3_EXTRA_TOOL_TOKENS` (comma-separated).
_C3_TOOL_TOKENS: tuple[str, ...] = (
    "pay", "payment", "transfer", "wire",
    "delete", "drop", "destroy",
    "terminate", "quarantine",
    "iac_destroy", "kubectl_delete",
)

# Read-verb prefixes — a tool whose name begins with any of these is
# a read operation regardless of what other tokens the name contains.
# Prefixes are matched WITH the trailing separator so `getter_config`
# (starts with `get`) doesn't match — only `get_config` does.
_READ_VERB_PREFIXES: tuple[str, ...] = (
    "get_", "list_", "check_", "describe_", "show_",
    "read_", "fetch_", "is_", "has_", "count_",
    "undelete_", "restore_", "recover_",
    # Metadata / info variants
    "info_", "stat_", "status_", "query_",
)


def _c3_token_pattern() -> _re.Pattern[str]:
    import os as _os
    extra = _os.getenv("ACP_C3_EXTRA_TOOL_TOKENS", "")
    tokens = list(_C3_TOOL_TOKENS) + [t.strip().lower() for t in extra.split(",") if t.strip()]
    # Token boundary: separator = any non-alphanumeric character. `_`,
    # `-`, `.`, `/` all count.
    boundary = r"(?<![A-Za-z0-9])"
    end      = r"(?![A-Za-z0-9])"
    return _re.compile(
        boundary + "(?:" + "|".join(_re.escape(t) for t in tokens) + ")" + end,
        flags=_re.IGNORECASE,
    )


_C3_RE = _c3_token_pattern()


def _is_read_verb_name(name_lower: str) -> bool:
    """True iff the tool name starts with a canonical read-verb prefix.
    This exemption survives even if the name contains a C3 token later
    (e.g. `get_pay_history`)."""
    return any(name_lower.startswith(p) for p in _READ_VERB_PREFIXES)


def _classify_incoming_anthropic_request(raw_body: bytes) -> str:
    """ATF §3.3 pre-classify — peek at the Anthropic request body to
    decide whether this is C3 (would trigger consistency sampling).

    Rules (in order):
      1. Malformed body → C2 (classification is a HINT, not a security
         boundary; the policy downstream still gates).
      2. Any tool whose name starts with a read-verb prefix contributes
         C2, not C3, even if the name contains a destructive token.
      3. Any remaining tool whose name contains a C3 token (word-boundary
         match, case-insensitive) → the whole request is C3.
      4. Otherwise → C2.
    """
    try:
        import json as _json_local
        body = _json_local.loads(raw_body)
        if not isinstance(body, dict):
            return "C2"
        tools = body.get("tools") or []
        if not isinstance(tools, list):
            return "C2"
        for t in tools:
            if not isinstance(t, dict):
                continue
            name = str(t.get("name", ""))
            name_lower = name.lower()
            if _is_read_verb_name(name_lower):
                continue  # rule 2 — read-verb-prefix exemption
            if _C3_RE.search(name):
                return "C3"
    except (ValueError, TypeError):
        pass
    return "C2"


# The route is mounted at `/messages` even though the customer's
# Anthropic SDK calls `/v1/messages`. The gateway's `/v1/*` alias
# middleware (services/gateway/main.py:479) strips the version prefix
# before FastAPI routes the request, so the bare path is what the
# router sees. Both `https://ha.aegisagent.in/v1/messages` (Anthropic
# SDK convention) and `https://ha.aegisagent.in/messages` (bare form)
# resolve here.
@router.post("/messages")
async def proxy_anthropic_messages(request: Request) -> Response:
    """Anthropic-compatible /v1/messages proxy with per-employee accounting.

    Auth: ``x-api-key`` carries an ``acp_emp_…`` virtual key minted via
    ``POST /api-keys/employees``.

    Pre-call: validates key → checks daily + monthly budget against
    Redis-stored spend → refuses with 402 if either cap would be
    exceeded. The corporate Anthropic key is NEVER touched on a
    refused call.

    Forward: dispatches to ``api.anthropic.com/v1/messages`` using the
    tenant's stored Anthropic key (``ACP_UPSTREAM_ANTHROPIC_KEY`` env
    var for now — Sprint 17.3 will move this to a per-tenant encrypted
    column). Response is returned to the SDK verbatim.

    Post-call: parses ``usage.input_tokens`` + ``usage.output_tokens``,
    multiplies by the model's per-1M rate, increments the Redis day +
    month counters, and pushes an audit event tagged with the employee
    email so the Sprint 17.3 /team UI can roll spend up per human.
    """
    # 1. extract auth
    auth_key = request.headers.get("x-api-key") or ""
    if not auth_key.startswith("acp_emp_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="x-api-key must be an Aegis employee virtual key (acp_emp_…)",
        )

    # 2. validate via the api service's /api-keys/validate HTTP endpoint
    # — same pattern the gateway uses everywhere else so it doesn't grow
    # a second DB connection. Returns the row's dict shape (id, tenant_id,
    # subject_kind, subject_email, daily_budget_usd, monthly_budget_usd, …).
    key_data = await service_client.validate_api_key(auth_key)
    if (
        key_data is None
        or not key_data.get("is_active", True)
        or key_data.get("subject_kind") != "employee"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid, revoked, or non-employee API key",
        )

    employee_email = key_data.get("subject_email") or ""
    if not employee_email:
        # Defensive — should never happen for subject_kind='employee'
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Employee key row missing subject_email",
        )

    tenant_id_str = str(key_data.get("tenant_id") or "")
    daily_budget_usd   = key_data.get("daily_budget_usd")
    monthly_budget_usd = key_data.get("monthly_budget_usd")
    key_role           = (key_data.get("role") or "DEVELOPER").upper()

    # Sprint EH-1: minimum role required to use the /v1/messages proxy is
    # DEVELOPER. Reject READ_ONLY keys here so they cannot run inference.
    # OWNER/ADMIN/SECURITY_ANALYST/DEVELOPER pass through.
    if key_role == "READ_ONLY":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API key role READ_ONLY cannot invoke /v1/messages",
        )

    # Pin the tenant + role onto request.state so internal_headers() picks
    # them up for downstream service calls. /v1/messages auths via
    # x-api-key not Bearer, so the gateway's normal Clerk middleware
    # doesn't set request.state for us.
    #
    # S5 (2026-07-21 audit P1-8): DO NOT wrap in try/except. Downstream
    # code reads these fields; a silent failure here is a fail-open at
    # the auth boundary.
    request.state.tenant_id = tenant_id_str
    request.state.role = key_role
    request.state.actor = f"apikey:{key_data.get('key_prefix', auth_key[:8])}"

    # 3. establish redis client. The budget check moved AFTER body parse
    # (step 5) because atomic reserve-and-reconcile needs the model + max
    # tokens to size the reservation — see S7 (audit P1-6).
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        # 4. upstream Anthropic key
        # Sprint 17.2 reads it from a single env var. Sprint 17.3 will
        # move this to a per-tenant encrypted column so each customer
        # supplies their own. Keeping it env-var-based here means a
        # single-tenant deploy works today without a schema change.
        upstream_key = getattr(settings, "UPSTREAM_ANTHROPIC_KEY", None) or ""
        if not upstream_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Aegis-for-Teams is not configured: UPSTREAM_ANTHROPIC_KEY "
                    "missing from the deployment. Set it in your environment + "
                    "redeploy, or contact your workspace OWNER."
                ),
            )

        # 5. read body verbatim — Anthropic schema is preserved untouched
        raw_body = await request.body()
        anthropic_version = request.headers.get(
            "anthropic-version", _ANTHROPIC_VERSION_DEFAULT,
        )
        try:
            req_json = await request.json() if raw_body else {}
        except Exception:
            req_json = {}
        model = (req_json or {}).get("model") or "claude-haiku-4-5"

        # 5a. budget reserve — atomic compare-and-charge (S7, audit P1-6).
        # Reserved amount is the maximum possible cost for this call; the
        # actual cost is reconciled after the LLM call by adding
        # (actual - reserved) via record_spend, which now accepts negatives.
        max_out = int((req_json or {}).get("max_tokens") or 4096)
        msg_text = str((req_json or {}).get("messages") or "")
        reserved_usd = cost_usd(model, max(1, len(msg_text) // 4), max_out)
        _reserve_status, day_val, month_val = await proxy_helpers.reserve_or_reject(
            redis, tenant_id_str, employee_email, reserved_usd,
            daily_budget_usd, monthly_budget_usd,
        )
        if _reserve_status == "d":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Daily LLM budget reached for {employee_email}: "
                    f"${day_val:.2f} / ${month_val:.2f}"
                ),
            )
        if _reserve_status == "m":
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Monthly LLM budget reached for {employee_email}: "
                    f"${day_val:.2f} / ${month_val:.2f}"
                ),
            )
        if _reserve_status == "err":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LLM budget accounting temporarily unavailable",
            )

        # 5b. prompt-injection scan — runs against the concatenation of
        # every user/system message text so we catch payloads regardless
        # of which turn they sit in. The corporate Anthropic key is NEVER
        # touched on a refused call; instead we write an audit row tagged
        # decision='deny' so harmful_blocked_30d on /team/overview lights
        # up. Sprint 17.7.
        scan_text_parts: list[str] = []
        for msg in (req_json.get("messages") or []):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                scan_text_parts.append(content)
            elif isinstance(content, list):
                # Anthropic SDK content blocks: [{type:"text", text:"…"}]
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        scan_text_parts.append(str(block.get("text") or ""))
        system_prompt = req_json.get("system")
        if isinstance(system_prompt, str):
            scan_text_parts.append(system_prompt)
        elif isinstance(system_prompt, list):
            for block in system_prompt:
                if isinstance(block, dict) and block.get("type") == "text":
                    scan_text_parts.append(str(block.get("text") or ""))

        scan_text = "\n".join(scan_text_parts)
        scan_result = InjectionDetector.scan(scan_text) if scan_text else None
        if scan_result is not None and not scan_result.allowed:
            pattern_name = (scan_result.metadata or {}).get("pattern", "unknown")
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="anthropic_messages",
                decision="deny",
                reason=scan_result.reason,
                metadata={
                    "employee_email": employee_email,
                    "model":          model,
                    "input_tokens":   0,
                    "output_tokens":  0,
                    "cost_usd":       0.0,
                    "status_code":    403,
                    "latency_ms":     0,
                    "anthropic_version": anthropic_version,
                    "findings":       scan_result.flags or ["prompt_injection"],
                    "risk_score":     scan_result.risk_score,
                    "match_pattern":  str(pattern_name)[:120],
                },
                request_id=request.headers.get("X-Request-ID"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "prompt_blocked",
                    "reason": scan_result.reason,
                    "findings": scan_result.flags or ["prompt_injection"],
                    "risk_score": scan_result.risk_score,
                },
            )

        # 5b-bis. Approval-replay shortcut. If the client received a
        # 202 earlier and the operator has since approved, the SDK
        # re-sends the same prompt with `X-Aegis-Approval-ID: <id>`.
        # We look it up; if the approval is `approved`, belongs to this
        # tenant, and matches this employee, we skip the escalation
        # scan and forward to Anthropic. Sprint 19 follow-up.
        replay_id = (request.headers.get("X-Aegis-Approval-ID") or "").strip()
        replay_approved = False
        if replay_id:
            record = await proxy_helpers.lookup_approval(request, tenant_id_str, replay_id)
            if record is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No approval with id {replay_id!r}",
                )
            if (record.get("employee_email") or "").lower() != employee_email.lower():
                # Approval belongs to a different employee — refuse.
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Approval does not belong to this employee",
                )
            if record.get("status") == "rejected":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={
                        "error":  "approval_rejected",
                        "reason": record.get("reason"),
                    },
                )
            if record.get("status") != "approved":
                # Still pending — refuse with the same 202 the SDK saw
                # originally so it knows to keep waiting.
                import json as _json
                return Response(
                    content=_json.dumps({
                        "status":          "pending_approval",
                        "approver_role":   record.get("approver_role"),
                        "matched_pattern": record.get("matched_pattern"),
                        "approval_id":     replay_id,
                        "reason":          "Still awaiting human approval",
                        "inbox_url":       "/approval-inbox",
                    }),
                    status_code=status.HTTP_202_ACCEPTED,
                    media_type="application/json",
                )
            replay_approved = True

        # 5c. high-risk-but-not-deny patterns — escalate to a human
        # approver instead of forwarding to Anthropic. Sprint 19.
        # The audit row tagged decision='escalate' shows up in the
        # Approval Inbox; the operator approves/rejects with a reason
        # via POST /autonomy/overrides, which lands in
        # human_override_events and ticks the Sprint 12 dashboard's
        # `escalations_prevented` KPI.
        #
        # Skip this gate entirely if the SDK is replaying with an
        # already-approved X-Aegis-Approval-ID — the operator has
        # explicitly cleared the same prompt and the audit row above
        # has it on record.
        # Base + pack-aware escalation scan. The base patterns (wire
        # $100k, kubectl prod, etc.) always run; the pack patterns
        # extend coverage when the tenant has enabled SOC2 / PCI /
        # HIPAA / Finance / DevOps.
        esc_pattern = None
        matched_pack_id: str | None = None
        matched_pack_controls: list[str] = []
        if not replay_approved:
            esc_pattern = escalation_patterns.scan(scan_text)
            if esc_pattern is None:
                enabled_packs = await proxy_helpers.fetch_enabled_policy_packs(
                    request, tenant_id_str,
                )
                pack_hit = policy_packs.scan_for_pack_escalation(
                    scan_text, enabled_packs,
                )
                if pack_hit is not None:
                    esc_pattern, matched_pack_id = pack_hit
                    pack = policy_packs.get(matched_pack_id)
                    if pack is not None:
                        matched_pack_controls = list(pack.framework_controls)
        if esc_pattern is not None:
            # The approval_id we hand back is the request_id — the
            # Approval Inbox already uses request_id as the resolution
            # key (ApprovalInbox.jsx:73-82). Generate one ourselves if
            # the caller's SDK didn't send X-Request-ID so the operator
            # always has a stable handle.
            approval_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            # U6 — stamp the tenant's current policy_version so the SDK
            # replay path can reject this approval if the policy is bumped
            # between escalation and approval.
            esc_policy_version = await proxy_helpers.get_current_policy_version(
                tenant_id_str,
            )
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="anthropic_messages",
                decision="escalate",
                reason=esc_pattern.label,
                metadata={
                    "employee_email":   employee_email,
                    "model":            model,
                    "input_tokens":     0,
                    "output_tokens":    0,
                    "cost_usd":         0.0,
                    "status_code":      202,
                    "latency_ms":       0,
                    "anthropic_version": anthropic_version,
                    "findings":         [f"Escalation:{esc_pattern.id}"],
                    "risk_score":       65.0,
                    "approver_role":    esc_pattern.approver_role,
                    "matched_pattern":  esc_pattern.id,
                    # Sprint 23 — when a pack rule contributed the
                    # match, surface the pack_id + the compliance
                    # framework controls it covers so the Compliance
                    # page can badge them correctly.
                    "policy_pack":      matched_pack_id,
                    "framework_controls": matched_pack_controls,
                    "prompt_excerpt":   scan_text[:240],
                    "policy_version":   esc_policy_version,
                },
                request_id=approval_id,
            )

            # Real-time UI feed: push the escalation onto the per-tenant SSE
            # channel so /events/stream subscribers (Dashboard, ApprovalInbox,
            # NotificationCenter) light up the moment a CFO/CISO approval is
            # queued, not on the next dashboard poll. Best-effort: SSE failure
            # never blocks the 202.
            try:
                await publish_event(
                    redis, tenant_id_str, "llm_proxy_escalate",
                    {
                        "approval_id":     approval_id,
                        "approver_role":   esc_pattern.approver_role,
                        "matched_pattern": esc_pattern.id,
                        "policy_pack":     matched_pack_id,
                        "employee_email":  employee_email,
                        "model":           model,
                    },
                )
            except Exception:  # noqa: BLE001
                pass

            # Sprint 21 — if the tenant has Slack approvals configured,
            # fire the webhook card with HMAC-signed Approve / Reject
            # links. Best-effort; the in-app Inbox is unaffected.
            slack_url, slack_secret = await proxy_helpers.fetch_tenant_slack_config(
                request, tenant_id_str,
            )
            slack_notified = False
            if slack_url and slack_secret:
                await proxy_helpers.post_slack_card(
                    webhook_url=slack_url,
                    secret=slack_secret,
                    tenant_id=tenant_id_str,
                    approval_id=approval_id,
                    approver_role=esc_pattern.approver_role,
                    matched_pattern=esc_pattern.id,
                    employee_email=employee_email,
                    prompt_excerpt=scan_text[:240],
                    base_url=_PUBLIC_BASE_URL,
                )
                slack_notified = True

            import json as _json
            return Response(
                content=_json.dumps({
                    "status":          "pending_approval",
                    "approver_role":   esc_pattern.approver_role,
                    "matched_pattern": esc_pattern.id,
                    "approval_id":     approval_id,
                    "reason":          esc_pattern.label,
                    "inbox_url":       "/approval-inbox",
                    "slack_notified":  slack_notified,
                }),
                status_code=status.HTTP_202_ACCEPTED,
                media_type="application/json",
            )

        # 6. forward to api.anthropic.com
        forward_headers = {
            "x-api-key":         upstream_key,
            "anthropic-version": anthropic_version,
            "Content-Type":      "application/json",
        }
        # Anthropic also accepts the `anthropic-beta` header for opt-in
        # features (extended-thinking, computer-use, etc.). Forward it if
        # the SDK sent one.
        beta = request.headers.get("anthropic-beta")
        if beta:
            forward_headers["anthropic-beta"] = beta

        # ATF §9.3 — Consistency Sampling for C3 (opt-in per tenant).
        # Cheap short-circuit: unless the tenant is in ACP_C3_SAMPLING_TENANTS,
        # skip classification + the extra HTTP entirely.
        from services.policy.c3_gate import evaluate as _c3_evaluate
        from services.policy.c3_gate import should_sample as _c3_should_sample
        # Pre-classify by peeking at the incoming request. Only C3-tagged
        # requests trigger the 3× upstream sampling — everything else
        # goes straight through.
        _pre_class = _classify_incoming_anthropic_request(raw_body)
        if _c3_should_sample(_pre_class, tenant_id_str):
            async def _plan_once() -> dict[str, object]:
                async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as _c:
                    _r = await _c.post(_ANTHROPIC_URL, content=raw_body, headers=forward_headers)
                _body = _r.json() if _r.headers.get("content-type", "").startswith("application/json") else {}
                # Constraint-relevant fingerprint = tool_use payload MINUS
                # the per-call `id` (Anthropic mints a fresh `toolu_...`
                # id every call — including it would make every sample
                # inconsistent by design, defeating the whole purpose of
                # §9.3 consistency sampling). We fingerprint on the
                # semantic content: name + input. That's what §9.3 calls
                # "the plan".
                _tools = [
                    {"name": b.get("name"), "input": b.get("input")}
                    for b in (_body.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "tool_use"
                ]
                return {"tool_calls": _tools}
            _c3_result = await _c3_evaluate(_plan_once)
            if _c3_result.decision == "BLOCK":
                logger.warning("c3_sampling_blocked",
                               tenant=tenant_id_str, reason=_c3_result.reason)
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"C3 consistency-sampling blocked: {_c3_result.reason}",
                )

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as client:
                upstream_resp = await client.post(
                    _ANTHROPIC_URL, content=raw_body, headers=forward_headers,
                )
        except httpx.HTTPError as exc:
            logger.error("llm_proxy_upstream_failed", error=str(exc))
            # Surface transport failures on the LiveFeed too — operators
            # need to see Anthropic-unreachable bursts the same way they
            # see throttle bursts. Best-effort; never blocks the 502.
            try:
                await publish_event(
                    redis, tenant_id_str, "llm_proxy_call",
                    {
                        "decision":       "rejected",
                        "status_code":    502,
                        "model":          model,
                        "employee_email": employee_email,
                        "input_tokens":   0,
                        "output_tokens":  0,
                        "cost_usd":       0.0,
                        "latency_ms":     int((time.monotonic() - t0) * 1000),
                        "reject_reason":  "client_aborted",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Anthropic upstream unreachable: {type(exc).__name__}",
            ) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        # 7. parse usage + meter spend (best-effort — never fails the
        # proxy if the post-call accounting hits a glitch).
        usage_input = 0
        usage_output = 0
        try:
            body_json = upstream_resp.json()
            if isinstance(body_json, dict):
                u = body_json.get("usage") or {}
                usage_input  = int(u.get("input_tokens")  or 0)
                usage_output = int(u.get("output_tokens") or 0)
        except Exception as exc:
            swallow_log(logger, "llm_usage_parse_failed", exc)

        call_cost = cost_usd(model, usage_input, usage_output)
        # S7 (audit P1-6): reconcile with the reserved amount from step 5a.
        await proxy_helpers.record_spend(
            redis, tenant_id_str, employee_email, call_cost - reserved_usd,
        )

        # 8. audit trail — this is the row that lights up the Sprint
        # 17.3 /team page + the cryptographic Merkle chain. Action name
        # 'llm_proxy_call' is dedicated so future filters can pick it
        # out without colliding with the existing tool-call decisions.
        # ATF §7.1 outcome.response_hash — SHA-256 of the upstream response body
        # so the ledger entry binds authorization to the actual returned payload.
        # Head-only would suffice for oversized responses; Anthropic replies fit.
        response_hash = "sha256:" + hashlib.sha256(upstream_resp.content).hexdigest()
        try:
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="anthropic_messages",
                decision="allow" if upstream_resp.is_success else "error",
                reason=None,
                metadata={
                    "employee_email": employee_email,
                    "model":          model,
                    "input_tokens":   usage_input,
                    "output_tokens":  usage_output,
                    "cost_usd":       call_cost,
                    "status_code":    upstream_resp.status_code,
                    "latency_ms":     int(latency_ms),
                    "anthropic_version": anthropic_version,
                    "response_hash":  response_hash,
                    # ATF §3.3 classify: LLM proxy = external comm + possible PII
                    # + reversible → C2 unless the tenant's model/prompt hits a
                    # C3 threshold (e.g. legal_commitment tools). Deterministic
                    # predicate, tunable per tenant in a later phase.
                    "action_class":   _atf_classify({
                        "mutation": True,
                        "external_communication": True,
                        "reversibility": "REVERSIBLE",
                        "resource_classification": "INTERNAL",
                    }),
                },
                request_id=request.headers.get("X-Request-ID"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_proxy_audit_failed", error=str(exc))

        # Real-time UI feed for allow / rejected / error paths. Same
        # channel as the escalate publish above so the Dashboard "Live
        # · N events" ticker increments on every Claude proxy call,
        # not just the /execute path.
        #
        # Decision tags:
        #   - "allow"    → 2xx success (call reached Anthropic + paid)
        #   - "rejected" → 401/403/429 — operator-visible upstream
        #                  block (bad upstream key, downstream WAF,
        #                  Anthropic rate-limit). Operators want this
        #                  on the LiveFeed during a throttle burst.
        #   - "error"    → 5xx or anything else (transport failures
        #                  are already surfaced via the 502 branch
        #                  above, so this is upstream 5xx territory).
        _status_code = upstream_resp.status_code
        if 200 <= _status_code < 300:
            _sse_decision: str = "allow"
            _reject_reason: str | None = None
        elif _status_code == 401:
            _sse_decision = "rejected"
            _reject_reason = "upstream_401"
        elif _status_code == 403:
            _sse_decision = "rejected"
            _reject_reason = "upstream_403"
        elif _status_code == 429:
            _sse_decision = "rejected"
            _reject_reason = "upstream_429"
        else:
            _sse_decision = "error"
            _reject_reason = None
        _sse_payload: dict[str, Any] = {
            "decision":        _sse_decision,
            "model":           model,
            "employee_email":  employee_email,
            "input_tokens":    usage_input,
            "output_tokens":   usage_output,
            "cost_usd":        call_cost,
            "status_code":     _status_code,
            "latency_ms":      int(latency_ms),
        }
        if _reject_reason is not None:
            _sse_payload["reject_reason"] = _reject_reason
        try:
            await publish_event(
                redis, tenant_id_str, "llm_proxy_call", _sse_payload,
            )
        except Exception:  # noqa: BLE001
            pass

        # 9. B-006 closure 2026-06-18 (Enterprise Security Review):
        # Happy-path (2xx) returns upstream verbatim so the Anthropic
        # SDK keeps working unchanged. Error-path (4xx/5xx) is wrapped
        # in the Aegis APIResponse envelope so Aegis-SDK consumers see
        # a uniform shape ({success:false, error, meta:{code, upstream:...}})
        # regardless of whether the failure came from Aegis (rate-limit,
        # budget cap, policy block) or from Anthropic (rate-limit, key
        # invalid, model unavailable). The raw upstream body is still
        # available under meta.upstream_body for SDKs that want to
        # passthrough Anthropic-specific error details.
        if 200 <= upstream_resp.status_code < 300:
            return Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                media_type=upstream_resp.headers.get(
                    "content-type", "application/json",
                ),
            )

        # Non-2xx: wrap in Aegis envelope.
        try:
            _upstream_body = upstream_resp.json()
        except Exception:  # noqa: BLE001
            _upstream_body = {"raw": upstream_resp.text[:500]}
        _upstream_error_type = None
        _upstream_message = None
        if isinstance(_upstream_body, dict):
            _err = _upstream_body.get("error")
            if isinstance(_err, dict):
                _upstream_error_type = _err.get("type")
                _upstream_message = _err.get("message")
        _aegis_envelope = {
            "success": False,
            "data": None,
            "error": _upstream_message or f"Anthropic upstream returned {upstream_resp.status_code}",
            "meta": {
                "code": upstream_resp.status_code,
                "upstream": "anthropic",
                "upstream_error_type": _upstream_error_type,
                "upstream_body": _upstream_body,
                "decision": _sse_decision,
                "reject_reason": _reject_reason,
            },
        }
        import json as _json
        return Response(
            content=_json.dumps(_aegis_envelope).encode(),
            status_code=upstream_resp.status_code,
            media_type="application/json",
        )
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────────────────────────────────────────────────
# Sprint 21 — Slack approve / reject callbacks.
#
# Each escalation card posted to the tenant's Slack carries two URLs
# of the form ``/slack/<decision>/<approval_id>?exp=…&sig=…&tenant_id=…``.
# When the CFO clicks one, Slack opens the link in a normal browser
# (no Slack app install needed). The handler verifies the HMAC, calls
# /autonomy/overrides exactly like the in-app inbox button, and
# returns a tiny standalone HTML page.
#
# Auth is the signature itself — no JWT — so the surface must be
# skip-listed in the gateway middleware (the path doesn't start with
# /v1, but middleware._SKIP_PATH_PREFIXES is a tuple of prefixes).
# ─────────────────────────────────────────────────────────────────────


async def _slack_decide(
    request: Request,
    approval_id: str,
    decision: str,           # 'approve' or 'reject'
) -> Response:
    qs = dict(request.query_params)
    tenant_id_str = qs.get("tenant_id") or ""
    sig = qs.get("sig") or ""
    try:
        exp = int(qs.get("exp") or "0")
    except (TypeError, ValueError):
        exp = 0

    if not tenant_id_str:
        return Response(
            content=slack_approvals.render_result_html(
                decision, approval_id, ok=False, reason="missing tenant",
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
            media_type="text/html; charset=utf-8",
        )

    # Pin tenant on request.state so internal_headers picks it up for
    # the audit-svc + autonomy-svc fan-out (same trick as /v1/messages).
    try:
        request.state.tenant_id = tenant_id_str
    except Exception:  # noqa: BLE001
        pass

    _, secret = await proxy_helpers.fetch_tenant_slack_config(request, tenant_id_str)
    if not secret:
        return Response(
            content=slack_approvals.render_result_html(
                decision, approval_id, ok=False,
                reason="Slack approvals not configured for this workspace",
            ),
            status_code=status.HTTP_403_FORBIDDEN,
            media_type="text/html; charset=utf-8",
        )

    ok = slack_approvals.verify_sig(
        approval_id=approval_id,
        decision=decision,
        tenant_id=tenant_id_str,
        exp=exp,
        secret=secret,
        sig=sig,
    )
    if not ok:
        return Response(
            content=slack_approvals.render_result_html(
                decision, approval_id, ok=False,
                reason="Signature invalid or link expired",
            ),
            status_code=status.HTTP_401_UNAUTHORIZED,
            media_type="text/html; charset=utf-8",
        )

    # Look up the approval and bail early if it's already been actioned
    # (idempotent — clicking the link twice should be safe).
    record = await proxy_helpers.lookup_approval(request, tenant_id_str, approval_id)
    if record is None:
        return Response(
            content=slack_approvals.render_result_html(
                decision, approval_id, ok=False, reason="approval not found",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
            media_type="text/html; charset=utf-8",
        )
    if record.get("status") in ("approved", "rejected"):
        # Show success either way — the operator's click was honoured
        # earlier; nothing changes.
        return Response(
            content=slack_approvals.render_result_html(
                record["status"][:-1], approval_id, ok=True,
            ),
            status_code=status.HTTP_200_OK,
            media_type="text/html; charset=utf-8",
        )

    # Land the override the same way the in-app inbox does.
    event_type = "approval" if decision == "approve" else "override"
    reason_str = (
        f"Slack {decision} by {record.get('approver_role') or 'operator'}"
    )
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            ov_resp = await client.post(
                f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/overrides",
                json={
                    "actor":       "slack-callback",
                    "actor_role":  record.get("approver_role") or "approver",
                    "event_type":  event_type,
                    "target_kind": "request",
                    "target_id":   approval_id,
                    "request_id":  approval_id,
                    "reason":      reason_str,
                    "metadata":    {"via": "slack", "decision": decision},
                },
                headers=internal_headers(request),
            )
        if ov_resp.status_code >= 400:
            logger.warning(
                "slack_override_post_failed",
                status=ov_resp.status_code,
                body=ov_resp.text[:200],
            )
            return Response(
                content=slack_approvals.render_result_html(
                    decision, approval_id, ok=False,
                    reason="autonomy service refused the override",
                ),
                status_code=status.HTTP_502_BAD_GATEWAY,
                media_type="text/html; charset=utf-8",
            )
    except httpx.HTTPError as exc:
        logger.warning("slack_override_post_error", error=str(exc))
        return Response(
            content=slack_approvals.render_result_html(
                decision, approval_id, ok=False,
                reason="autonomy service unreachable",
            ),
            status_code=status.HTTP_502_BAD_GATEWAY,
            media_type="text/html; charset=utf-8",
        )

    return Response(
        content=slack_approvals.render_result_html(decision, approval_id, ok=True),
        status_code=status.HTTP_200_OK,
        media_type="text/html; charset=utf-8",
    )


@router.get("/slack/approve/{approval_id}")
async def slack_approve(approval_id: str, request: Request) -> Response:
    return await _slack_decide(request, approval_id, "approve")


@router.get("/slack/reject/{approval_id}")
async def slack_reject(approval_id: str, request: Request) -> Response:
    return await _slack_decide(request, approval_id, "reject")
