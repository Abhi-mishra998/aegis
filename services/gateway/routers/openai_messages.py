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
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, Response, status

from sdk.common.audit_stream import push_audit_event
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from services.gateway.openai_pricing import cost_usd
from services.gateway.client import service_client
from services.gateway._helpers import internal_headers
from services.gateway.inference_proxy import InjectionDetector
from services.gateway import escalation_patterns
from services.gateway import slack_approvals
from services.policy import packs as policy_packs

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["llm-proxy"])

_OPENAI_URL = "https://api.openai.com/v1/chat/completions"
_UPSTREAM_TIMEOUT_S = 60.0
_PUBLIC_BASE_URL = (
    getattr(settings, "PUBLIC_BASE_URL", "") or "https://ha.aegisagent.in"
)


# ─────────────────────────────────────────────────────────────────────
# Helpers — same shape as the Anthropic proxy. We deliberately don't
# share the helpers from messages.py because the imports would create
# a circular reference: this module is mounted alongside the Anthropic
# one. The 30 lines of duplication is acceptable for the isolation.
# ─────────────────────────────────────────────────────────────────────


def _spend_key_day(tenant_id: str, email: str, day: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{day}"


def _spend_key_month(tenant_id: str, email: str, month: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{month}"


async def _current_spend(redis, tenant_id: str, email: str) -> tuple[float, float]:
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        today_raw = await redis.get(_spend_key_day(tenant_id, email, day_str))
        month_raw = await redis.get(_spend_key_month(tenant_id, email, month_str))
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai_spend_read_failed", error=str(exc))
        return 0.0, 0.0
    return (float(today_raw or 0), float(month_raw or 0))


async def _record_spend(redis, tenant_id: str, email: str, cost: float) -> None:
    if cost <= 0:
        return
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        await redis.incrbyfloat(_spend_key_day(tenant_id, email, day_str), cost)
        await redis.incrbyfloat(_spend_key_month(tenant_id, email, month_str), cost)
        # Day buckets live 7d, month buckets live 70d — well past any
        # billing reconciliation window.
        await redis.expire(_spend_key_day(tenant_id, email, day_str), 7 * 24 * 3600)
        await redis.expire(_spend_key_month(tenant_id, email, month_str), 70 * 24 * 3600)
    except Exception as exc:  # noqa: BLE001
        logger.warning("openai_spend_record_failed", error=str(exc))


async def _lookup_approval(
    request: Request, tenant_id: str, approval_id: str,
) -> dict | None:
    """Re-implementation of the helper from messages.py for module
    isolation. Returns the normalized approval record or None."""
    headers = internal_headers(request)
    esc_row: dict | None = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.post(
                f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/search",
                json={"decision": "escalate", "limit": 50},
                headers=headers,
            )
        if resp.status_code == 200:
            data = (resp.json() or {}).get("data") or {}
            items = data.get("items", []) if isinstance(data, dict) else []
            for r in items:
                if (r.get("request_id") or "") == approval_id:
                    esc_row = r
                    break
    except httpx.HTTPError as exc:
        logger.warning("approval_lookup_audit_failed", error=str(exc))

    if esc_row is None:
        return None

    meta = esc_row.get("metadata_json") or esc_row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json as _json
            meta = _json.loads(meta)
        except Exception:
            meta = {}

    status_str = "pending"
    decided_at = decided_by = reason = None
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            ov_resp = await client.get(
                f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/overrides",
                params={
                    "minutes": 43200, "target_kind": "request",
                    "target_id": approval_id, "limit": 10,
                },
                headers=headers,
            )
        if ov_resp.status_code == 200:
            ov_body = ov_resp.json() or {}
            ov_items = ov_body.get("data") if isinstance(ov_body, dict) else ov_body
            if isinstance(ov_items, list) and ov_items:
                ov = ov_items[0]
                et = (ov.get("event_type") or "").lower()
                if et == "approval":
                    status_str = "approved"
                elif et == "override":
                    status_str = "rejected"
                decided_at = ov.get("occurred_at") or None
                decided_by = ov.get("actor") or None
                reason = ov.get("reason") or None
    except httpx.HTTPError as exc:
        logger.warning("approval_lookup_override_failed", error=str(exc))

    return {
        "approval_id":     approval_id,
        "status":          status_str,
        "approver_role":   meta.get("approver_role"),
        "matched_pattern": meta.get("matched_pattern"),
        "employee_email":  meta.get("employee_email"),
        "requested_at":    esc_row.get("timestamp") or esc_row.get("created_at"),
        "decided_at":      decided_at,
        "decided_by":      decided_by,
        "reason":          reason,
        "prompt_excerpt":  meta.get("prompt_excerpt"),
    }


async def _fetch_enabled_policy_packs(
    request: Request, tenant_id: str,
) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/workspace/policy-packs",
                headers=internal_headers(request),
            )
        if resp.status_code != 200:
            return []
        body = resp.json() or {}
        data = body.get("data") if isinstance(body, dict) else None
        return list((data or {}).get("enabled") or [])
    except httpx.HTTPError as exc:
        logger.warning("policy_packs_fetch_failed", error=str(exc))
        return []


async def _fetch_tenant_slack_config(
    request: Request, tenant_id: str,
) -> tuple[str | None, str | None]:
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(
                f"{settings.IDENTITY_SERVICE_URL.rstrip('/')}/workspace/slack-config",
                headers=internal_headers(request),
            )
        if resp.status_code != 200:
            return (None, None)
        body = resp.json() or {}
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return (None, None)
        return (data.get("webhook_url") or None, data.get("signing_secret") or None)
    except httpx.HTTPError as exc:
        logger.warning("slack_config_fetch_failed", error=str(exc))
        return (None, None)


async def _post_slack_card(
    *,
    webhook_url: str,
    secret: str,
    tenant_id: str,
    approval_id: str,
    approver_role: str,
    matched_pattern: str,
    employee_email: str,
    prompt_excerpt: str,
) -> None:
    try:
        card = slack_approvals.build_slack_card(
            base_url=_PUBLIC_BASE_URL,
            tenant_id=tenant_id,
            secret=secret,
            approval_id=approval_id,
            approver_role=approver_role,
            matched_pattern=matched_pattern,
            employee_email=employee_email,
            prompt_excerpt=prompt_excerpt,
            requested_at_iso=datetime.now(tz=timezone.utc).isoformat(),
        )
        async with httpx.AsyncClient(timeout=4.0) as client:
            await client.post(webhook_url, json=card)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack_card_post_failed", error=str(exc))


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
        today_usd, month_usd = await _current_spend(redis, tenant_id_str, employee_email)
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
            record = await _lookup_approval(request, tenant_id_str, replay_id)
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
                enabled_packs = await _fetch_enabled_policy_packs(
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
            slack_url, slack_secret = await _fetch_tenant_slack_config(
                request, tenant_id_str,
            )
            slack_notified = False
            if slack_url and slack_secret:
                await _post_slack_card(
                    webhook_url=slack_url, secret=slack_secret,
                    tenant_id=tenant_id_str, approval_id=approval_id,
                    approver_role=esc_pattern.approver_role,
                    matched_pattern=esc_pattern.id,
                    employee_email=employee_email,
                    prompt_excerpt=scan_text[:240],
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
        await _record_spend(redis, tenant_id_str, employee_email, call_cost)

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
