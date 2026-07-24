"""Sprint 17.3–21 — Aegis-for-Teams dashboard, approvals + replay views.

Split out of services/gateway/routers/messages.py in S19 (2026-07-21).
The Anthropic /v1/messages proxy + Slack approve/reject callbacks stay
in messages.py; every read-side team + approval endpoint lives here.

Same router mount point (no prefix); registered in gateway/main.py
alongside `_messages_router`.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Request, status

from sdk.common.config import settings
from sdk.common.redis import get_redis_client
from sdk.common.response import APIResponse
from services.gateway import proxy_helpers
from services.gateway._helpers import internal_headers
from services.gateway.client import service_client

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["llm-proxy"])


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
        uuid.UUID(tenant_id_str)
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
                today_usd, month_usd = await proxy_helpers.current_spend(
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
    from datetime import timedelta

    start_iso = (
        datetime.now(tz=UTC) - timedelta(days=30)
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
    now = datetime.now(tz=UTC)
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
    from datetime import timedelta
    start_iso = (
        datetime.now(tz=UTC) - timedelta(days=30)
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
            # Sprint 15 — Replay deep-link target. Every audit row
            # carries request_id; surfacing it here lets the
            # EmployeeProfile recent-calls table link to /replay/<id>.
            "request_id":    r.get("request_id") or "",
        })

    requests_30d = len(employee_rows)

    # 4.  Live spend counters from Redis (fast-path for the budget bars).
    redis = get_redis_client(settings.REDIS_URL, decode_responses=True)
    try:
        today_usd, month_usd = await proxy_helpers.current_spend(redis, tenant_id_str, email_lc)
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
    now = datetime.now(tz=UTC)
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

    from datetime import timedelta
    start_iso = (
        datetime.now(tz=UTC) - timedelta(days=30)
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

    # 2c. /autonomy/overrides — fan-out to autonomy-svc so we can split
    # the bare `escalated` count into pending / approved / rejected.
    # The Approval Inbox already uses the same join (matched by
    # request_id). Sprint 19 follow-up. The 30-day window is 43200
    # minutes — same upper bound the autonomy router accepts.
    approved_ids: set[str] = set()
    rejected_ids: set[str] = set()
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            ov_resp = await client.get(
                f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/overrides",
                params={"minutes": 43200, "limit": 1000},
                headers=headers,
            )
        if ov_resp.status_code == 200:
            ov_body = ov_resp.json() or {}
            ov_items = ov_body.get("data") if isinstance(ov_body, dict) else ov_body
            if isinstance(ov_items, list):
                for o in ov_items:
                    rid = (o.get("request_id") or "").strip()
                    if not rid:
                        continue
                    et = (o.get("event_type") or "").lower()
                    if et == "approval":
                        approved_ids.add(rid)
                    elif et == "override":
                        rejected_ids.add(rid)
    except httpx.HTTPError as exc:
        logger.warning("dashboard_overrides_failed", error=str(exc))

    # 3. Per-row business-value aggregate.
    records_protected = 0
    dollar_risk = 0.0
    distinct_controls: set[str] = set()
    escalate_request_ids: set[str] = set()

    for r in rows:
        decision = (r.get("decision") or "").lower()
        meta = r.get("metadata_json") or r.get("metadata") or {}
        if isinstance(meta, str):
            try:
                import json as _json
                meta = _json.loads(meta)
            except Exception:
                meta = {}

        # Track every escalate row's request_id so we can intersect
        # with the approval / rejection sets pulled from autonomy-svc.
        if decision == "escalate":
            rid = (r.get("request_id") or "").strip()
            if rid:
                escalate_request_ids.add(rid)

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

    # Sprint 19 follow-up — split escalations into pending / approved /
    # rejected by intersecting the escalate-row request_ids (from the
    # /logs scan above, capped at 1000 — so this is best-effort for
    # tenants with >1000 audit rows in 30d; the bare `escalated` count
    # in mandate_kpis is authoritative since it comes from the
    # /logs/aggregate server-side count). When the per-row pull missed
    # an escalation but autonomy-svc still resolved it, surface that
    # as approved/rejected too via the union.
    seen_esc_ids = escalate_request_ids | approved_ids | rejected_ids
    escalations_approved = len(approved_ids & seen_esc_ids)
    escalations_rejected = len(rejected_ids & seen_esc_ids)
    # Pending = (all escalations) – (already approved or rejected).
    # Floor at zero in case the aggregate count is lower than the per-
    # row scan (different windows; aggregate is wider).
    escalations_pending = max(0, escalated - escalations_approved - escalations_rejected)

    return APIResponse(data={
        "mandate_kpis": {
            "protected_agents":  protected_agents,
            "actions_evaluated": actions_evaluated,
            "allowed":           allowed,
            "denied":            denied,
            "escalated":         escalated,
            "active_findings":   findings_count,
        },
        "escalation_breakdown": {
            "pending":  escalations_pending,
            "approved": escalations_approved,
            "rejected": escalations_rejected,
        },
        "business_value": {
            "records_protected_estimate":   records_protected,
            # Sprint 19 — restate against the actual approver workflow.
            # An escalation that was approved DOES count as prevented
            # because the agent didn't run it autonomously; a rejected
            # one obviously counts too. A pending one is still in
            # flight so we don't credit it.
            "escalations_prevented":        escalations_approved + escalations_rejected,
            "compliance_controls_enforced": len(distinct_controls),
            "dollar_risk_mitigated_usd":    round(dollar_risk, 2),
        },
        "window_days": 30,
    })


# ─────────────────────────────────────────────────────────────────────
# Sprint 19 follow-up — Approval resume API. Two surfaces:
#
#   1. GET /approvals/{approval_id}/status — the SDK calls this to
#      poll the state of an approval it received via the 202 response
#      from /v1/messages. Returns pending / approved / rejected +
#      decided_at + reason + approver_role + matched_pattern, all
#      scoped to the caller's tenant.
#
#   2. /v1/messages now accepts the X-Aegis-Approval-ID header. If the
#      approval exists, belongs to this tenant, and is currently
#      approved, the escalation scan is bypassed and the prompt is
#      forwarded to Anthropic. The original 202 + this 200-on-replay
#      flow is the CFO-approved-the-wire pattern the founder asked
#      for.
#
# The approval store is the existing audit_logs (decision='escalate')
# joined with human_override_events (event_type='approval' /
# 'override'). We don't introduce a new table — append-only audit is
# the single source of truth.
# ─────────────────────────────────────────────────────────────────────


# _lookup_approval lives in services/gateway/proxy_helpers.py — both
# the Anthropic + OpenAI proxies + this status handler use the same
# join across audit_logs + human_override_events.


@router.get("/approvals/{approval_id}/status")
async def approval_status(approval_id: str, request: Request) -> APIResponse[dict]:
    """Approval status lookup — dual-auth (JWT operator OR employee SDK key).

    Operator path: Authorization: Bearer <Clerk JWT> — tenant comes
    from the JWT's aegis_tenant_id claim (stamped by the gateway
    middleware before this handler runs).

    SDK path: x-api-key: acp_emp_… — the same employee virtual key the
    SDK sent to /v1/messages. We validate it the same way, then refuse
    if the approval doesn't belong to that employee. The middleware
    skip-list lets /v1/approvals through without JWT, so when the path
    arrives here via the /v1/* alias prefix-strip we still match this
    handler.
    """
    auth_key = request.headers.get("x-api-key") or ""
    if auth_key.startswith("acp_emp_"):
        # SDK / employee-key path.
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
        tenant_id_str = str(key_data.get("tenant_id") or "")
        employee_email = (key_data.get("subject_email") or "").strip().lower()
        try:
            request.state.tenant_id = tenant_id_str
        except Exception:  # noqa: BLE001
            pass

        record = await proxy_helpers.lookup_approval(request, tenant_id_str, approval_id.strip())
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No approval with id {approval_id!r}",
            )
        if (record.get("employee_email") or "").lower() != employee_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Approval does not belong to this employee",
            )
        return APIResponse(data=record)

    # JWT / operator path — the gateway middleware already validated
    # the token and stamped X-Tenant-ID before this handler ran.
    tenant_id_str = (
        request.headers.get("X-Tenant-ID")
        or (getattr(request.state, "jwt_claims", {}) or {}).get("tenant_id", "")
    )
    if not tenant_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context missing — please sign in again.",
        )

    record = await proxy_helpers.lookup_approval(request, tenant_id_str, approval_id.strip())
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No approval with id {approval_id!r}",
        )
    return APIResponse(data=record)


# ─────────────────────────────────────────────────────────────────────
# Sprint 15 — Unified replay. One URL: /replay/{request_id}. Joins
# every audit row for the request (typically one decision per
# request_id + any human_override_events that resolved it) into a
# 5-stage stepper payload the UI renders left-to-right.
#
# The handler is tenant-scoped via the gateway middleware's JWT auth
# (path is in _MANAGEMENT_PATH_PREFIXES — see middleware.py).
# ─────────────────────────────────────────────────────────────────────


@router.get("/replay/{request_id}")
async def replay_request(request_id: str, request: Request) -> APIResponse[dict]:
    """Return the full audit timeline for one request_id.

    Shape::

        {
          "request_id": str,
          "stages": [
            {"kind": "user_request",       ...},   # 1
            {"kind": "agent_decision",     ...},   # 2
            {"kind": "tool_request",       ...},   # 3
            {"kind": "aegis_evaluation",   ...},   # 4
            {"kind": "outcome",            ...},   # 5
          ],
          "audit_rows":     [...],
          "override_events": [...],
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

    rid = request_id.strip()
    headers = internal_headers(request)

    # 1. Every audit row with this request_id (most have exactly one;
    # some flows write a follow-on row when the operator approves).
    audit_rows: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.post(
                f"{settings.AUDIT_SERVICE_URL.rstrip('/')}/logs/search",
                json={"limit": 50},
                headers=headers,
            )
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or {}
            items = data.get("items", []) if isinstance(data, dict) else []
            audit_rows = [x for x in items if (x.get("request_id") or "") == rid]
    except httpx.HTTPError as exc:
        logger.warning("replay_audit_fetch_failed", error=str(exc))

    # 2. Override events keyed by request_id (operator approve / reject /
    # kill / note).
    override_events: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(
                f"{settings.AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/overrides",
                params={"minutes": 43200, "target_kind": "request", "target_id": rid, "limit": 20},
                headers=headers,
            )
        if r.status_code == 200:
            body = r.json() or {}
            items = body.get("data") if isinstance(body, dict) else body
            if isinstance(items, list):
                override_events = items
    except httpx.HTTPError as exc:
        logger.warning("replay_overrides_fetch_failed", error=str(exc))

    if not audit_rows and not override_events:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No audit history for request_id {rid!r}",
        )

    # Pick the primary audit row — earliest by timestamp.
    audit_rows.sort(key=lambda x: str(x.get("timestamp") or x.get("created_at") or ""))
    primary = audit_rows[0] if audit_rows else {}
    meta = primary.get("metadata_json") or primary.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json as _json
            meta = _json.loads(meta)
        except Exception:
            meta = {}

    # Derive stage fields once.
    employee_email   = meta.get("employee_email") or ""
    model            = meta.get("model") or ""
    matched_pattern  = meta.get("matched_pattern") or ""
    approver_role    = meta.get("approver_role") or ""
    decision         = (primary.get("decision") or "").lower()
    policy_pack      = meta.get("policy_pack")
    framework_ctrls  = meta.get("framework_controls") or []
    findings         = meta.get("findings") or []
    risk_score       = meta.get("risk_score")
    prompt_excerpt   = meta.get("prompt_excerpt") or meta.get("reason") or ""
    tool_name        = primary.get("tool") or ""
    action_name      = primary.get("action") or ""
    status_code      = meta.get("status_code")
    latency_ms       = meta.get("latency_ms")
    input_tokens     = meta.get("input_tokens")
    output_tokens    = meta.get("output_tokens")
    cost_usd         = meta.get("cost_usd")
    upstream_provider = meta.get("upstream_provider") or (
        "anthropic" if tool_name == "anthropic_messages" else (
            "openai" if tool_name == "openai_chat_completions" else None
        )
    )

    # Map override events into a stable shape.
    overrides_view = []
    for o in override_events:
        overrides_view.append({
            "event_type": (o.get("event_type") or "").lower(),
            "actor":      o.get("actor"),
            "actor_role": o.get("actor_role"),
            "reason":     o.get("reason"),
            "occurred_at": o.get("occurred_at"),
            "metadata":   o.get("metadata_json") or o.get("metadata") or {},
        })

    # Resolution = the latest override's event_type.
    resolution = None
    if overrides_view:
        latest = overrides_view[-1]
        et = latest.get("event_type") or ""
        if et == "approval":
            resolution = "approved"
        elif et == "override":
            resolution = "rejected"
        elif et:
            resolution = et

    # ── Build the 5-stage stepper ──────────────────────────────────
    stages = [
        {
            "kind":  "user_request",
            "label": "User request",
            "icon":  "user",
            "employee_email": employee_email,
            "prompt_excerpt": prompt_excerpt,
            "at":     primary.get("timestamp") or primary.get("created_at"),
        },
        {
            "kind":  "agent_decision",
            "label": "Agent decision",
            "icon":  "bot",
            "model": model,
            "upstream_provider": upstream_provider,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "cost_usd":      cost_usd,
        },
        {
            "kind":  "tool_request",
            "label": "Tool / proxy call",
            "icon":  "crosshair",
            "tool":   tool_name,
            "action": action_name,
        },
        {
            "kind":  "aegis_evaluation",
            "label": "Aegis evaluation",
            "icon":  "shield",
            "decision":           decision,
            "matched_pattern":    matched_pattern,
            "approver_role":      approver_role,
            "policy_pack":        policy_pack,
            "framework_controls": framework_ctrls,
            "findings":           findings,
            "risk_score":         risk_score,
            "latency_ms":         latency_ms,
        },
        {
            "kind":  "outcome",
            "label": "Outcome",
            "icon":  "flag",
            "status_code":    status_code,
            "resolution":     resolution,
            "override_events": overrides_view,
        },
    ]

    return APIResponse(data={
        "request_id":      rid,
        "stages":          stages,
        "audit_rows":      audit_rows,
        "override_events": override_events,
    })


