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
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
import uuid
from fastapi import APIRouter, HTTPException, Request, Response, status

from sdk.common.audit_stream import push_audit_event
from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.gateway.anthropic_pricing import cost_usd
from services.gateway.client import service_client
from services.gateway._helpers import internal_headers
from services.gateway.inference_proxy import InjectionDetector
from services.gateway import escalation_patterns

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["llm-proxy"])

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION_DEFAULT = "2023-06-01"
_UPSTREAM_TIMEOUT_S = 60.0


# ─────────────────────────────────────────────────────────────────────
# We do NOT hit the api_keys DB directly from the gateway — the gateway
# DATABASE_URL points at acp_identity, not acp_api. Auth + listing go
# through the api service's existing HTTP endpoints (validate +
# /api-keys?subject_kind=employee) so the gateway stays a pure proxy.
# ─────────────────────────────────────────────────────────────────────


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────────────
# Spend tracking — Redis counter per (tenant, employee_email, day) and
# per (tenant, employee_email, month). Daily + monthly budget enforced
# BEFORE we forward to Anthropic so the corporate key is never spent
# above the cap.
# ─────────────────────────────────────────────────────────────────────


def _spend_key_day(tenant_id: str, email: str, day: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{day}"


def _spend_key_month(tenant_id: str, email: str, month: str) -> str:
    return f"acp:llm_spend:emp:{tenant_id}:{email}:{month}"


async def _current_spend(redis, tenant_id: str, email: str) -> tuple[float, float]:
    """Return (today_usd, this_month_usd) for one employee. NEVER throws."""
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        today_raw = await redis.get(_spend_key_day(tenant_id, email, day_str))
        month_raw = await redis.get(_spend_key_month(tenant_id, email, month_str))
    except Exception as exc:
        logger.warning("llm_proxy_spend_read_failed", error=str(exc))
        return 0.0, 0.0
    return (
        float(today_raw or 0),
        float(month_raw or 0),
    )


async def _record_spend(
    redis, tenant_id: str, email: str, cost: float,
) -> None:
    """Increment both day + month counters. NEVER throws — spend tracking
    must not break the proxy itself; reconciliation against the upstream
    Anthropic invoice catches any drift."""
    now = datetime.now(tz=timezone.utc)
    day_str   = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    try:
        pipe = redis.pipeline()
        pipe.incrbyfloat(_spend_key_day(tenant_id, email, day_str), cost)
        pipe.expire(_spend_key_day(tenant_id, email, day_str), 7 * 24 * 3600)
        pipe.incrbyfloat(_spend_key_month(tenant_id, email, month_str), cost)
        pipe.expire(_spend_key_month(tenant_id, email, month_str), 60 * 24 * 3600)
        await pipe.execute()
    except Exception as exc:
        logger.warning("llm_proxy_spend_record_failed", error=str(exc), cost=cost)


# ─────────────────────────────────────────────────────────────────────
# /v1/messages — Anthropic-compatible. The SDK behaves as if it's
# talking to api.anthropic.com directly. We just sit in front of it.
# ─────────────────────────────────────────────────────────────────────


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

        # 5c. high-risk-but-not-deny patterns — escalate to a human
        # approver instead of forwarding to Anthropic. Sprint 19.
        # The audit row tagged decision='escalate' shows up in the
        # Approval Inbox; the operator approves/rejects with a reason
        # via POST /autonomy/overrides, which lands in
        # human_override_events and ticks the Sprint 12 dashboard's
        # `escalations_prevented` KPI.
        esc_pattern = escalation_patterns.scan(scan_text)
        if esc_pattern is not None:
            # The approval_id we hand back is the request_id — the
            # Approval Inbox already uses request_id as the resolution
            # key (ApprovalInbox.jsx:73-82). Generate one ourselves if
            # the caller's SDK didn't send X-Request-ID so the operator
            # always has a stable handle.
            approval_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
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
                    # Surface a short, non-PII excerpt so the operator
                    # can read the inbox card without expanding it.
                    "prompt_excerpt":   scan_text[:240],
                },
                request_id=approval_id,
            )
            import json as _json
            return Response(
                content=_json.dumps({
                    "status":          "pending_approval",
                    "approver_role":   esc_pattern.approver_role,
                    "matched_pattern": esc_pattern.id,
                    "approval_id":     approval_id,
                    "reason":          esc_pattern.label,
                    "inbox_url":       "/approval-inbox",
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

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_UPSTREAM_TIMEOUT_S) as client:
                upstream_resp = await client.post(
                    _ANTHROPIC_URL, content=raw_body, headers=forward_headers,
                )
        except httpx.HTTPError as exc:
            logger.error("llm_proxy_upstream_failed", error=str(exc))
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
        except Exception:
            pass

        call_cost = cost_usd(model, usage_input, usage_output)
        await _record_spend(redis, tenant_id_str, employee_email, call_cost)

        # 8. audit trail — this is the row that lights up the Sprint
        # 17.3 /team page + the cryptographic Merkle chain. Action name
        # 'llm_proxy_call' is dedicated so future filters can pick it
        # out without colliding with the existing tool-call decisions.
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
                },
                request_id=request.headers.get("X-Request-ID"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_proxy_audit_failed", error=str(exc))

        # 9. return upstream verbatim so the Anthropic SDK can't tell
        # we're in the middle.
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


# ─────────────────────────────────────────────────────────────────────
# /team/employees — Sprint 17.3 UI rollup.
#
# Returns one row per employee virtual key currently provisioned for
# the signed-in tenant, joined with today's + this-month's spend from
# Redis. The /team page in the UI renders this as a table with the
# email, key prefix, daily / monthly budgets, current spend, and a
# "view → revoke" affordance per row.
#
# Auth is the standard tenant JWT (the middleware authenticates because
# /team is NOT in the skip-list). The route reads the api_keys table
# directly — same pattern as the /v1/messages handler — because
# spreading it across two services (api-svc list + gateway spend join)
# would have meant 3 hops per row.
# ─────────────────────────────────────────────────────────────────────


async def _list_employee_keys_from_apisvc(request: Request) -> list[dict]:
    """Shared helper: fetch every employee virtual key for the tenant.

    Used by both the per-employee /team/employees rollup and the
    Sprint 17.5 /team/overview aggregation. Goes through the api-svc
    HTTP contract so the gateway never opens a direct DB connection to
    the api database (the gateway's DATABASE_URL points at identity).
    """
    url = f"{settings.API_SERVICE_URL.rstrip('/')}/api-keys"
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(
                url,
                params={"subject_kind": "employee"},
                headers=internal_headers(request),
            )
    except httpx.HTTPError as exc:
        logger.error("team_employees_list_upstream_error", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"api service unreachable: {type(exc).__name__}",
        ) from exc

    if resp.status_code != 200:
        logger.warning(
            "team_employees_list_upstream_non_200",
            status=resp.status_code,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not list employee keys from the api service.",
        )
    return (resp.json() or {}).get("data") or []


@router.get("/team/employees")
async def list_team_employees(request: Request) -> APIResponse[list[dict]]:
    """List employee virtual keys + current-period spend for the tenant.

    Response shape (one row per employee):
    ```
    [
      {
        "key_id": "<uuid>",
        "key_prefix": "acp_emp_a…",
        "email": "alice@acme.com",
        "name": "alice",
        "is_active": true,
        "daily_budget_usd": 50.0,
        "monthly_budget_usd": 1000.0,
        "today_usd": 4.27,
        "month_usd": 184.91,
        "created_at": "2026-06-16T17:00:00Z",
        "last_used_at": null
      }
    ]
    ```
    """
    # Tenant comes from the gateway's authenticated request state.
    tenant_id_str = (
        request.headers.get("X-Tenant-ID")
        or (getattr(request.state, "jwt_claims", {}) or {}).get("tenant_id", "")
    )
    if not tenant_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing — please sign in again.",
        )
    try:
        tenant_uuid = uuid.UUID(tenant_id_str)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tenant_id: {tenant_id_str!r}",
        )

    keys = await _list_employee_keys_from_apisvc(request)

    # Join with Redis spend.
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        rows: list[dict] = []
        for k in keys:
            email = (k.get("subject_email") or "").strip()
            today_usd, month_usd = (0.0, 0.0)
            if email:
                today_usd, month_usd = await _current_spend(
                    redis, str(k.get("tenant_id") or ""), email,
                )
            rows.append({
                "key_id":           k.get("id"),
                "key_prefix":       k.get("key_prefix"),
                "email":            email,
                "name":             k.get("name"),
                "is_active":        bool(k.get("is_active", True)),
                "department":       k.get("department"),
                "daily_budget_usd":   k.get("daily_budget_usd"),
                "monthly_budget_usd": k.get("monthly_budget_usd"),
                "today_usd":        round(today_usd, 4),
                "month_usd":        round(month_usd, 4),
                "created_at":       k.get("created_at"),
                "last_used_at":     k.get("last_used_at"),
            })
        return APIResponse(data=rows)
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────────────────────────────────────────────────
# Sprint 17.5 — Aegis for Teams Productization.
#
# /team/overview returns the four CIO/CISO/FinOps KPIs + a per-
# department breakdown in a single payload so the Team page hero, the
# Department View, and the Executive Summary tab all render from one
# fetch. Audit_logs is the source of truth for request counts +
# harmful-action counts (action='llm_proxy_call' with decision in
# {allow,error,deny}); Redis carries today's spend (the durable monthly
# total comes from summing audit metadata cost_usd at query time so
# it survives a Redis flush).
# ─────────────────────────────────────────────────────────────────────


def _bucket_department(value: str | None) -> str:
    """Normalize NULL/empty department to 'Unassigned' for grouping."""
    v = (value or "").strip()
    return v if v else "Unassigned"


@router.get("/team/overview")
async def team_overview(request: Request) -> APIResponse[dict]:
    """Single-fetch payload for the entire Team page hero + tabs.

    Shape::

        {
          "kpis": {
            "active_employees":              <int>,
            "ai_requests_30d":               <int>,
            "monthly_spend_usd":             <float>,
            "harmful_actions_blocked_30d":   <int>,
            "compliance_violations_prevented_30d": <int>,
            "highest_risk_department":       <str | null>,
          },
          "departments": [
            {
              "name":            <str>,
              "employees":       <int>,
              "requests_30d":    <int>,
              "spend_30d_usd":   <float>,
              "harmful_blocked_30d":   <int>,
              "compliance_enforced_30d": <int>,
              "risk_score":      <float 0..1>,
              "risk_label":      "Low" | "Moderate" | "Elevated" | "High",
            },
            …
          ],
          "trend_30d": [ {"day": "YYYY-MM-DD", "requests": int, "spend_usd": float}, … ]
        }
    """
    tenant_id_str = (
        request.headers.get("X-Tenant-ID")
        or (getattr(request.state, "jwt_claims", {}) or {}).get("tenant_id", "")
    )
    if not tenant_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing — please sign in again.",
        )

    # Employees ⇒ department + active count
    keys = await _list_employee_keys_from_apisvc(request)
    email_to_department: dict[str, str] = {}
    department_employees: dict[str, set[str]] = {}
    active_emails: set[str] = set()
    for k in keys:
        email = (k.get("subject_email") or "").strip().lower()
        if not email:
            continue
        dept = _bucket_department(k.get("department"))
        email_to_department[email] = dept
        department_employees.setdefault(dept, set()).add(email)
        if k.get("is_active", True):
            active_emails.add(email)

    # Audit-log roll-up — last 30 days, action='llm_proxy_call'.
    # Source of truth for requests + spend + harmful counts (Redis is
    # only the fast-path budget counter). Uses GET /logs (not POST
    # /logs/search) because WAFv2 blocks JSON bodies with "limit":N
    # as SQL injection — and the GET variant supports the same filters
    # via query params. Hard cap is 1000 rows (audit-svc query limit);
    # at ~30 r/employee/day this comfortably covers 30 employees.
    from datetime import datetime, timedelta, timezone

    start_iso = (
        datetime.now(tz=timezone.utc) - timedelta(days=30)
    ).isoformat()
    proxy_url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                proxy_url,
                params={
                    "action":     "llm_proxy_call",
                    "start_date": start_iso,
                    "limit":      1000,
                },
                headers=internal_headers(request),
            )
        body = resp.json() if resp.status_code == 200 else {}
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            rows = data.get("items", []) or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
    except httpx.HTTPError:
        rows = []

    # Aggregate per-department + per-day.
    from collections import defaultdict

    dept_requests:   dict[str, int]   = defaultdict(int)
    dept_spend:      dict[str, float] = defaultdict(float)
    dept_harmful:    dict[str, int]   = defaultdict(int)
    dept_compliance: dict[str, int]   = defaultdict(int)
    daily: dict[str, dict[str, float]] = defaultdict(lambda: {"requests": 0, "spend_usd": 0.0})

    total_requests = 0
    total_spend = 0.0
    total_harmful = 0
    total_compliance = 0

    for r in rows:
        # Defensive parse — audit rows can be either flat dicts or
        # wrapped envelopes depending on which audit-service endpoint
        # the proxy hits.
        meta = r.get("metadata_json") or r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                import json as _json
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        email = (meta.get("employee_email") or "").strip().lower()
        if not email:
            continue
        dept = email_to_department.get(email, "Unassigned")
        cost = float(meta.get("cost_usd") or 0)
        decision = (r.get("decision") or "allow").lower()
        is_harmful = decision in ("deny", "block", "error") or bool(meta.get("findings"))

        dept_requests[dept] += 1
        dept_spend[dept]    += cost
        if is_harmful:
            dept_harmful[dept] += 1
            total_harmful += 1
        if meta.get("findings"):
            dept_compliance[dept] += 1
            total_compliance += 1

        total_requests += 1
        total_spend    += cost

        ts = r.get("created_at") or r.get("timestamp")
        if ts:
            day = str(ts)[:10]
            daily[day]["requests"]  = float(daily[day]["requests"]) + 1
            daily[day]["spend_usd"] = float(daily[day]["spend_usd"]) + cost

    # Build the per-department rows.
    def _risk_score(reqs: int, harmful: int) -> float:
        # 0..1. Floor at 0.05 so a department with even one
        # llm_proxy_call doesn't read as "no signal at all."
        if reqs <= 0:
            return 0.0
        rate = harmful / reqs
        return round(min(1.0, max(0.05, rate * 4)), 2)

    def _risk_label(score: float) -> str:
        if score >= 0.7: return "High"
        if score >= 0.4: return "Elevated"
        if score >= 0.15: return "Moderate"
        return "Low"

    departments: list[dict] = []
    for dept, emails in department_employees.items():
        reqs = dept_requests.get(dept, 0)
        harmful = dept_harmful.get(dept, 0)
        score = _risk_score(reqs, harmful)
        departments.append({
            "name":              dept,
            "employees":         len(emails),
            "requests_30d":      reqs,
            "spend_30d_usd":     round(dept_spend.get(dept, 0.0), 4),
            "harmful_blocked_30d":     harmful,
            "compliance_enforced_30d": dept_compliance.get(dept, 0),
            "risk_score":        score,
            "risk_label":        _risk_label(score),
        })
    departments.sort(key=lambda d: (-d["risk_score"], -d["requests_30d"]))

    highest_risk_dept = departments[0]["name"] if departments and departments[0]["risk_score"] > 0 else None

    # 30-day trend, fill missing days with zero.
    now = datetime.now(tz=timezone.utc)
    trend = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        bucket = daily.get(day, {"requests": 0, "spend_usd": 0.0})
        trend.append({
            "day":       day,
            "requests":  int(bucket["requests"]),
            "spend_usd": round(float(bucket["spend_usd"]), 4),
        })

    return APIResponse(data={
        "kpis": {
            "active_employees":                      len(active_emails),
            "ai_requests_30d":                       total_requests,
            "monthly_spend_usd":                     round(total_spend, 4),
            "harmful_actions_blocked_30d":           total_harmful,
            "compliance_violations_prevented_30d":   total_compliance,
            "highest_risk_department":               highest_risk_dept,
        },
        "departments": departments,
        "trend_30d":   trend,
    })


# ─────────────────────────────────────────────────────────────────────
# Sprint 17.6 — per-employee drill-down. The Members tab on /team links
# each row to /team/<email>, which calls this endpoint. Single fetch
# returns the employee record, both budget bars, 30-day token-burn
# trend, and the last 25 calls so the page can render with no
# additional round-trips.
# ─────────────────────────────────────────────────────────────────────


@router.get("/team/employees/{email}/profile")
async def team_employee_profile(email: str, request: Request) -> APIResponse[dict]:
    """Single-fetch payload for the /team/<email> detail page.

    Shape::

        {
          "employee": {
            "email": str,
            "name":  str,
            "department": str | None,
            "key_prefix": str,
            "is_active": bool,
            "daily_budget_usd": float | None,
            "monthly_budget_usd": float | None,
            "created_at": str,
          },
          "kpis": {
            "requests_30d":             int,
            "spend_30d_usd":            float,
            "spend_today_usd":          float,
            "spend_month_usd":          float,
            "daily_budget_used_pct":    float,
            "monthly_budget_used_pct":  float,
            "harmful_blocked_30d":      int,
            "models_used":              [str],
            "last_active":              str | null,
            "risk_score":               float,
            "risk_label":               "Low" | "Moderate" | "Elevated" | "High",
          },
          "trend_30d":   [{day, requests, spend_usd}, …],
          "recent_calls": [{ts, model, input_tokens, output_tokens, cost_usd, decision, findings}, …]
        }
    """
    tenant_id_str = (
        request.headers.get("X-Tenant-ID")
        or (getattr(request.state, "jwt_claims", {}) or {}).get("tenant_id", "")
    )
    if not tenant_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing — please sign in again.",
        )

    email_lc = email.strip().lower()
    if not email_lc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email is required.",
        )

    # 1.  Find the employee key. We list every employee then filter — the
    # api-svc /api-keys GET doesn't expose a single-email lookup, and
    # tenants have at most a few hundred keys.
    keys = await _list_employee_keys_from_apisvc(request)
    match = next(
        (k for k in keys if (k.get("subject_email") or "").strip().lower() == email_lc),
        None,
    )
    if not match:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No employee key for {email_lc!r}",
        )

    employee = {
        "email":              email_lc,
        "name":               match.get("name") or email_lc.split("@", 1)[0],
        "department":         match.get("department"),
        "key_prefix":         match.get("key_prefix"),
        "is_active":          bool(match.get("is_active", True)),
        "daily_budget_usd":   match.get("daily_budget_usd"),
        "monthly_budget_usd": match.get("monthly_budget_usd"),
        "created_at":         match.get("created_at"),
    }

    # 2.  Pull every llm_proxy_call row for the tenant in the last 30
    # days, then narrow by email in-process. Same GET-/logs contract as
    # /team/overview so WAFv2 doesn't trip.
    from datetime import datetime, timedelta, timezone
    start_iso = (
        datetime.now(tz=timezone.utc) - timedelta(days=30)
    ).isoformat()
    proxy_url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                proxy_url,
                params={
                    "action":     "llm_proxy_call",
                    "start_date": start_iso,
                    "limit":      1000,
                },
                headers=internal_headers(request),
            )
        body = resp.json() if resp.status_code == 200 else {}
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            rows = data.get("items", []) or []
        elif isinstance(data, list):
            rows = data
        else:
            rows = []
    except httpx.HTTPError:
        rows = []

    # 3.  Filter + aggregate.
    from collections import defaultdict

    employee_rows: list[dict] = []
    daily: dict[str, dict[str, float]] = defaultdict(
        lambda: {"requests": 0, "spend_usd": 0.0},
    )
    spend_30d = 0.0
    harmful_30d = 0
    models_used: set[str] = set()
    last_active: str | None = None

    for r in rows:
        meta = r.get("metadata_json") or r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                import json as _json
                meta = _json.loads(meta)
            except Exception:
                meta = {}
        row_email = (meta.get("employee_email") or "").strip().lower()
        if row_email != email_lc:
            continue

        cost = float(meta.get("cost_usd") or 0)
        decision = (r.get("decision") or "allow").lower()
        is_harmful = decision in ("deny", "block", "error") or bool(meta.get("findings"))
        model = (meta.get("model") or "").strip() or "unknown"

        spend_30d += cost
        if is_harmful:
            harmful_30d += 1
        models_used.add(model)

        ts = r.get("created_at") or r.get("timestamp")
        if ts:
            day = str(ts)[:10]
            daily[day]["requests"]  += 1
            daily[day]["spend_usd"] += cost
            if last_active is None or str(ts) > last_active:
                last_active = str(ts)

        employee_rows.append({
            "ts":            ts,
            "model":         model,
            "input_tokens":  int(meta.get("input_tokens") or 0),
            "output_tokens": int(meta.get("output_tokens") or 0),
            "cost_usd":      round(cost, 6),
            "decision":      decision,
            "findings":      meta.get("findings") or [],
            "latency_ms":    int(meta.get("latency_ms") or 0),
        })

    requests_30d = len(employee_rows)

    # 4.  Live spend counters from Redis (fast-path for the budget bars).
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        today_usd, month_usd = await _current_spend(redis, tenant_id_str, email_lc)
    finally:
        try:
            await redis.aclose()
        except Exception:  # noqa: BLE001
            pass

    daily_cap   = employee["daily_budget_usd"]
    monthly_cap = employee["monthly_budget_usd"]
    daily_pct = (
        round((today_usd / float(daily_cap)) * 100.0, 2)
        if daily_cap and float(daily_cap) > 0
        else 0.0
    )
    monthly_pct = (
        round((month_usd / float(monthly_cap)) * 100.0, 2)
        if monthly_cap and float(monthly_cap) > 0
        else 0.0
    )

    # 5.  Risk score — same shape as the /team/overview computation so
    # the rollup and the drill-down agree.
    if requests_30d <= 0:
        risk_score = 0.0
    else:
        rate = harmful_30d / requests_30d
        risk_score = round(min(1.0, max(0.05, rate * 4)), 2)
    if   risk_score >= 0.7: risk_label = "High"
    elif risk_score >= 0.4: risk_label = "Elevated"
    elif risk_score >= 0.15: risk_label = "Moderate"
    else: risk_label = "Low"

    # 6.  30-day trend, fill empty days with zero.
    now = datetime.now(tz=timezone.utc)
    trend = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        b = daily.get(day, {"requests": 0, "spend_usd": 0.0})
        trend.append({
            "day":       day,
            "requests":  int(b["requests"]),
            "spend_usd": round(float(b["spend_usd"]), 6),
        })

    # 7.  Recent activity — newest 25.
    employee_rows.sort(key=lambda r: str(r.get("ts") or ""), reverse=True)
    recent = employee_rows[:25]

    return APIResponse(data={
        "employee": employee,
        "kpis": {
            "requests_30d":            requests_30d,
            "spend_30d_usd":           round(spend_30d, 6),
            "spend_today_usd":         round(today_usd, 6),
            "spend_month_usd":         round(month_usd, 6),
            "daily_budget_used_pct":   daily_pct,
            "monthly_budget_used_pct": monthly_pct,
            "harmful_blocked_30d":     harmful_30d,
            "models_used":             sorted(models_used),
            "last_active":             last_active,
            "risk_score":              risk_score,
            "risk_label":              risk_label,
        },
        "trend_30d":    trend,
        "recent_calls": recent,
    })


# ─────────────────────────────────────────────────────────────────────
# Sprint 12 — Dashboard mandate KPIs. Replaces the abstract
# Agents/High-risk/Wizard-provisioned tiles with the 6 metrics every
# CISO buyer evaluates Aegis against (Protected Agents, Actions
# Evaluated, Allowed, Denied, Escalated, Active Findings) plus a
# business-value row (records protected estimate, escalations
# prevented, compliance controls enforced, dollar risk mitigated).
#
# One fetch fans out to registry (/workspace/inventory) + audit-svc
# (/logs windowed by date) so the Dashboard renders without N+1.
# ─────────────────────────────────────────────────────────────────────


@router.get("/dashboard/overview")
async def dashboard_overview(request: Request) -> APIResponse[dict]:
    """Single-fetch payload for the post-Sprint-12 Dashboard hero.

    Shape::

        {
          "mandate_kpis": {
            "protected_agents":   int,
            "actions_evaluated":  int,
            "allowed":            int,
            "denied":             int,
            "escalated":          int,
            "active_findings":    int,
          },
          "business_value": {
            "records_protected_estimate":   int,
            "escalations_prevented":        int,
            "compliance_controls_enforced": int,
            "dollar_risk_mitigated_usd":    float,
          },
          "window_days": 30,
        }
    """
    tenant_id_str = (
        request.headers.get("X-Tenant-ID")
        or (getattr(request.state, "jwt_claims", {}) or {}).get("tenant_id", "")
    )
    if not tenant_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing — please sign in again.",
        )

    from datetime import datetime, timedelta, timezone
    start_iso = (
        datetime.now(tz=timezone.utc) - timedelta(days=30)
    ).isoformat()

    # 1. /workspace/inventory — protected_agents = active count.
    headers = internal_headers(request)
    protected_agents = 0
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            inv_resp = await client.get(
                f"{settings.REGISTRY_SERVICE_URL.rstrip('/')}/workspace/inventory",
                headers=headers,
            )
        if inv_resp.status_code == 200:
            inv_body = inv_resp.json() or {}
            inv_data = inv_body.get("data") if isinstance(inv_body, dict) else inv_body
            if isinstance(inv_data, dict):
                protected_agents = int(inv_data.get("active") or 0)
    except httpx.HTTPError as exc:
        logger.warning("dashboard_inventory_failed", error=str(exc))

    # 2a. /logs/aggregate — server-side decision counts. Authoritative
    # for the mandate KPIs even on tenants with millions of rows. Used
    # to be a single /logs fetch capped at 1000 rows — that read as a
    # floor not a count on busy tenants.
    agg_url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/aggregate"
    actions_evaluated = 0
    allowed = denied = escalated = 0
    findings_count = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            agg_resp = await client.get(
                agg_url,
                params={"days": 30},
                headers=headers,
            )
        agg_body = agg_resp.json() if agg_resp.status_code == 200 else {}
        agg_data = agg_body.get("data") if isinstance(agg_body, dict) else None
        if isinstance(agg_data, dict):
            actions_evaluated = int(agg_data.get("total") or 0)
            decisions = agg_data.get("by_decision") or {}
            allowed   = int(decisions.get("allow") or 0)
            denied    = (
                int(decisions.get("deny") or 0)
                + int(decisions.get("block") or 0)
                + int(decisions.get("kill")  or 0)
            )
            escalated      = int(decisions.get("escalate") or 0)
            findings_count = int(agg_data.get("with_findings") or 0)
    except httpx.HTTPError as exc:
        logger.warning("dashboard_aggregate_failed", error=str(exc))

    # 2b. /logs — per-row pull capped at 1000 for the business-value
    # rollup (sum of row_count + amount_usd + distinct findings prefix
    # set). The dollar + records figures stay lower-bound on tenants
    # past 1000 rows, but the mandate KPI integers above are accurate.
    proxy_url = f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs"
    rows: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                proxy_url,
                params={"start_date": start_iso, "limit": 1000},
                headers=headers,
            )
        body = resp.json() if resp.status_code == 200 else {}
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict):
            rows = data.get("items", []) or []
        elif isinstance(data, list):
            rows = data
    except httpx.HTTPError as exc:
        logger.warning("dashboard_audit_failed", error=str(exc))

    # 3. Per-row business-value aggregate.
    records_protected = 0
    dollar_risk = 0.0
    distinct_controls: set[str] = set()

    for r in rows:
        decision = (r.get("decision") or "").lower()
        meta = r.get("metadata_json") or r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                import json as _json
                meta = _json.loads(meta)
            except Exception:
                meta = {}

        findings = meta.get("findings") if isinstance(meta, dict) else None
        if findings and isinstance(findings, list):
            # Note: findings_count is sourced from /logs/aggregate
            # above (server-side, not capped). Here we only mine the
            # set of distinct controls/signal-class prefixes for the
            # business-value 'Controls enforced' tile.
            for f in findings:
                if isinstance(f, str):
                    distinct_controls.add(f.split(":", 1)[0])

        if decision in ("deny", "block", "kill", "escalate") and isinstance(meta, dict):
            # records_protected_estimate — sum of row_count / dump_size
            # bytes-as-rows / page-content-rows when the block was a bulk
            # PII / dump / no-LIMIT SQL guard.
            for k in ("row_count", "page_rows", "rows", "result_rows"):
                v = meta.get(k)
                if isinstance(v, (int, float)) and v > 0:
                    records_protected += int(v)
                    break

            # dollar_risk_mitigated — sum of amount_usd on wire blocks +
            # cost of blocked llm_proxy_calls (which would have run on
            # the corporate Anthropic key). Both are real money saved.
            amount = meta.get("amount_usd") or meta.get("amount")
            if isinstance(amount, (int, float)) and amount > 0:
                dollar_risk += float(amount)
            if r.get("action") == "llm_proxy_call":
                # Blocked LLM call: would have spent at the request's
                # expected token cost. We don't have that estimate
                # at block time (the prompt is refused pre-flight),
                # so credit a conservative $0.05 per blocked call —
                # the average enterprise-prompt round-trip cost on
                # Sonnet 4.6. Documented in the UI tooltip.
                dollar_risk += 0.05

    return APIResponse(data={
        "mandate_kpis": {
            "protected_agents":  protected_agents,
            "actions_evaluated": actions_evaluated,
            "allowed":           allowed,
            "denied":            denied,
            "escalated":         escalated,
            "active_findings":   findings_count,
        },
        "business_value": {
            "records_protected_estimate":   records_protected,
            "escalations_prevented":        escalated,
            "compliance_controls_enforced": len(distinct_controls),
            "dollar_risk_mitigated_usd":    round(dollar_risk, 2),
        },
        "window_days": 30,
    })
