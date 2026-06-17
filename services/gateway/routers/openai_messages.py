"""Sprint 22 — OpenAI-compatible /v1/chat/completions proxy.

Same architecture as the Anthropic proxy (services/gateway/routers/
messages.py): per-employee virtual key auth, daily/monthly USD budget
check, prompt-injection deny scan, escalation pattern scan with
Slack notification + Approval Inbox + replay header, audit row +
spend metering.

Customer integration is two lines:

    import openai
    client = openai.OpenAI(
        api_key="acp_emp_…",
        base_url="https://ha.aegisagent.in/v1",
    )
    client.chat.completions.create(model="gpt-4o-mini", messages=[…])

From the OpenAI SDK's perspective nothing changed. From Aegis's
perspective every call is gated + audited + attributed per-employee.

The corporate OpenAI API key never reaches the employee — it lives
in SSM (``/aegis-prodha/openai/upstream-key``) and the gateway adds
it to the upstream request inside this handler.
"""
from __future__ import annotations

import time
import uuid

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from sdk.common.audit_stream import push_audit_event
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway.openai_pricing import cost_usd
from services.gateway.client import service_client
from services.gateway.inference_proxy import InjectionDetector
from services.gateway import escalation_patterns
from services.gateway import proxy_helpers
from services.gateway._helpers import publish_event
from services.policy import packs as policy_packs

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["llm-proxy"])

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_UPSTREAM_TIMEOUT_S = 60.0
_PUBLIC_BASE_URL = (
    getattr(settings, "PUBLIC_BASE_URL", "") or "https://ha.aegisagent.in"
)


# All per-employee bookkeeping + approval lookup + Slack + policy-pack
# fetch lives in services/gateway/proxy_helpers.py — both proxies
# share it. See the 2026-06-17 dead-code audit (SPRINT.md ledger).


# ─────────────────────────────────────────────────────────────────────
# The proxy route. Mounted at /chat/completions because the gateway's
# /v1/* alias middleware strips the version prefix; OpenAI SDKs hit
# /v1/chat/completions which arrives here as /chat/completions.
# ─────────────────────────────────────────────────────────────────────


@router.post("/chat/completions")
async def proxy_openai_chat_completions(request: Request) -> Response:
    """OpenAI-compatible chat completions with per-employee accounting.

    Auth: the OpenAI SDK sends ``Authorization: Bearer acp_emp_…``. We
    accept that and also fall back to ``x-api-key`` for parity with
    the Anthropic proxy.
    """
    # 1. extract auth — try Bearer first (OpenAI SDK convention), then
    # x-api-key (Aegis-house convention).
    auth_header = request.headers.get("authorization") or ""
    auth_key = ""
    if auth_header.lower().startswith("bearer "):
        auth_key = auth_header[7:].strip()
    if not auth_key:
        auth_key = (request.headers.get("x-api-key") or "").strip()
    if not auth_key.startswith("acp_emp_"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization must be an Aegis employee virtual key (acp_emp_…)",
        )

    # 2. validate via api-svc
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
    employee_email = (key_data.get("subject_email") or "").strip()
    if not employee_email:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Employee key row missing subject_email",
        )
    tenant_id_str      = str(key_data.get("tenant_id") or "")
    daily_budget_usd   = key_data.get("daily_budget_usd")
    monthly_budget_usd = key_data.get("monthly_budget_usd")
    try:
        request.state.tenant_id = tenant_id_str
    except Exception:  # noqa: BLE001
        pass

    # 3. budget pre-check
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        today_usd, month_usd = await proxy_helpers.current_spend(redis, tenant_id_str, employee_email)
        if daily_budget_usd is not None and today_usd >= float(daily_budget_usd):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Daily LLM budget reached for {employee_email}: "
                    f"${today_usd:.2f} / ${float(daily_budget_usd):.2f}"
                ),
            )
        if monthly_budget_usd is not None and month_usd >= float(monthly_budget_usd):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    f"Monthly LLM budget reached for {employee_email}: "
                    f"${month_usd:.2f} / ${float(monthly_budget_usd):.2f}"
                ),
            )

        # 4. upstream OpenAI key — fetched here but the missing-key
        # 503 fires LATER, only on the forward path. Deny + escalate
        # decisions still run so a customer testing prompts before
        # they configure the key gets accurate gating, not a confusing
        # 'not configured' for every call.
        upstream_key = getattr(settings, "UPSTREAM_OPENAI_KEY", None) or ""

        # 5. parse body
        raw_body = await request.body()
        try:
            req_json = await request.json() if raw_body else {}
        except Exception:
            req_json = {}
        model = (req_json or {}).get("model") or "gpt-4o-mini"

        # 5b. prompt-injection deny scan — concatenate every user /
        # assistant / system message text.
        scan_text_parts: list[str] = []
        for msg in (req_json.get("messages") or []):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                scan_text_parts.append(content)
            elif isinstance(content, list):
                # OpenAI vision-style content blocks:
                # [{type:"text",text:"…"},{type:"image_url",...}]
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        scan_text_parts.append(str(block.get("text") or ""))
        scan_text = "\n".join(scan_text_parts)

        # 5b-bis. Approval-replay shortcut.
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

        # Deny scan — runs after replay check so an approved replay
        # isn't re-blocked.
        scan_result = (
            InjectionDetector.scan(scan_text)
            if scan_text and not replay_approved
            else None
        )
        if scan_result is not None and not scan_result.allowed:
            pattern_name = (scan_result.metadata or {}).get("pattern", "unknown")
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="openai_chat_completions",
                decision="deny",
                reason=scan_result.reason,
                metadata={
                    "employee_email":   employee_email,
                    "model":            model,
                    "input_tokens":     0,
                    "output_tokens":    0,
                    "cost_usd":         0.0,
                    "status_code":      403,
                    "latency_ms":       0,
                    "findings":         scan_result.flags or ["prompt_injection"],
                    "risk_score":       scan_result.risk_score,
                    "match_pattern":    str(pattern_name)[:120],
                    "upstream_provider": "openai",
                },
                request_id=request.headers.get("X-Request-ID"),
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error":      "prompt_blocked",
                    "reason":     scan_result.reason,
                    "findings":   scan_result.flags or ["prompt_injection"],
                    "risk_score": scan_result.risk_score,
                },
            )

        # Base + pack-aware escalation scan.
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
            approval_id = (
                request.headers.get("X-Request-ID") or str(uuid.uuid4())
            )
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="openai_chat_completions",
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
                    "findings":         [f"Escalation:{esc_pattern.id}"],
                    "risk_score":       65.0,
                    "approver_role":    esc_pattern.approver_role,
                    "matched_pattern":  esc_pattern.id,
                    "policy_pack":      matched_pack_id,
                    "framework_controls": matched_pack_controls,
                    "prompt_excerpt":   scan_text[:240],
                    "upstream_provider": "openai",
                },
                request_id=approval_id,
            )
            # Real-time UI feed: mirror messages.py — surface OpenAI-proxy
            # escalations on the per-tenant SSE channel so /events/stream
            # consumers update without a poll.
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
                        "upstream":        "openai",
                    },
                )
            except Exception:  # noqa: BLE001
                pass
            slack_url, slack_secret = await proxy_helpers.fetch_tenant_slack_config(
                request, tenant_id_str,
            )
            slack_notified = False
            if slack_url and slack_secret:
                await proxy_helpers.post_slack_card(
                    webhook_url=slack_url, secret=slack_secret,
                    tenant_id=tenant_id_str, approval_id=approval_id,
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

        # 6. forward to api.openai.com — only here do we require the
        # upstream key, so the deny + escalate scans above can run
        # even on a half-configured workspace.
        if not upstream_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Aegis OpenAI proxy is not configured: "
                    "UPSTREAM_OPENAI_KEY missing from the deployment. "
                    "Set it in SSM at /aegis-prodha/openai/upstream-key + "
                    "restart the gateway, or contact your workspace OWNER."
                ),
            )
        forward_headers = {
            "Authorization": f"Bearer {upstream_key}",
            "Content-Type":  "application/json",
        }
        # OpenAI also accepts OpenAI-Organization / OpenAI-Project /
        # OpenAI-Beta headers. Forward them if the SDK sent any.
        for h in ("openai-organization", "openai-project", "openai-beta"):
            v = request.headers.get(h)
            if v:
                forward_headers[h] = v

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as client:
                upstream_resp = await client.post(
                    _OPENAI_URL, content=raw_body, headers=forward_headers,
                )
        except httpx.HTTPError as exc:
            logger.error("openai_upstream_failed", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"OpenAI upstream unreachable: {type(exc).__name__}",
            ) from exc
        latency_ms = (time.monotonic() - t0) * 1000

        # 7. meter usage
        usage_input = 0
        usage_output = 0
        try:
            body_json = upstream_resp.json()
            if isinstance(body_json, dict):
                u = body_json.get("usage") or {}
                # OpenAI's field names: prompt_tokens + completion_tokens
                usage_input  = int(u.get("prompt_tokens")     or 0)
                usage_output = int(u.get("completion_tokens") or 0)
        except Exception:
            pass
        call_cost = cost_usd(model, usage_input, usage_output)
        await proxy_helpers.record_spend(redis, tenant_id_str, employee_email, call_cost)

        # 8. audit trail
        try:
            await push_audit_event(
                redis=redis,
                tenant_id=tenant_id_str,
                agent_id=None,
                action="llm_proxy_call",
                tool="openai_chat_completions",
                decision="allow" if upstream_resp.is_success else "error",
                reason=None,
                metadata={
                    "employee_email":   employee_email,
                    "model":            model,
                    "input_tokens":     usage_input,
                    "output_tokens":    usage_output,
                    "cost_usd":         call_cost,
                    "status_code":      upstream_resp.status_code,
                    "latency_ms":       int(latency_ms),
                    "upstream_provider": "openai",
                },
                request_id=request.headers.get("X-Request-ID"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("openai_proxy_audit_failed", error=str(exc))

        # Real-time UI feed — same channel as the Claude proxy so the
        # Dashboard ticker fires regardless of upstream provider.
        try:
            await publish_event(
                redis, tenant_id_str, "llm_proxy_call",
                {
                    "decision":        "allow" if upstream_resp.is_success else "error",
                    "model":           model,
                    "employee_email":  employee_email,
                    "input_tokens":    usage_input,
                    "output_tokens":   usage_output,
                    "cost_usd":        call_cost,
                    "status_code":     upstream_resp.status_code,
                    "latency_ms":      int(latency_ms),
                    "upstream":        "openai",
                },
            )
        except Exception:  # noqa: BLE001
            pass

        # 9. passthrough
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get(
                "content-type", "application/json",
            ),
        )
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass
