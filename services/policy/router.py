from __future__ import annotations

import asyncio
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from sdk.common.auth import verify_internal_secret
from sdk.common.background import safe_bg as _safe_bg
from sdk.common.config import settings as policy_settings
from sdk.common.db import get_tenant_id
from sdk.common.deadline import check_deadline
from sdk.common.response import APIResponse
from services.policy.opa_client import opa_client
from services.policy.schemas import (
    EvaluationRequest,
    EvaluationResponse,
    SimulateDiffItem,
    SimulateRequest,
    SimulateResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/policy", tags=["policy"])


# Persistent clients — lifecycle managed via startup/shutdown
_REGISTRY_TIMEOUT = httpx.Timeout(3.0, connect=1.5)
_AUDIT_TIMEOUT = httpx.Timeout(2.0, connect=1.0)
_registry_client: httpx.AsyncClient | None = None
_audit_client: httpx.AsyncClient | None = None


def init_policy_clients() -> None:
    """Initialize persistent global clients at startup."""
    global _registry_client, _audit_client
    if _registry_client is None or _registry_client.is_closed:
        _registry_client = httpx.AsyncClient(timeout=_REGISTRY_TIMEOUT)
    if _audit_client is None or _audit_client.is_closed:
        _audit_client = httpx.AsyncClient(timeout=_AUDIT_TIMEOUT)


def get_registry_client() -> httpx.AsyncClient:
    """Return the global registry client. Initialize if needed (redundancy)."""
    global _registry_client
    if _registry_client is None or _registry_client.is_closed:
        _registry_client = httpx.AsyncClient(timeout=_REGISTRY_TIMEOUT)
    return _registry_client


def get_audit_client() -> httpx.AsyncClient:
    """Return the global audit client. Initialize if needed (redundancy)."""
    global _audit_client
    if _audit_client is None or _audit_client.is_closed:
        _audit_client = httpx.AsyncClient(timeout=_AUDIT_TIMEOUT)
    return _audit_client


async def close_policy_clients() -> None:
    """Call during FastAPI shutdown lifespan to close persistent connections."""
    if _registry_client and not _registry_client.is_closed:
        await _registry_client.aclose()
    if _audit_client and not _audit_client.is_closed:
        await _audit_client.aclose()


# =========================
# HELPERS
# =========================


async def _fetch_agent(tenant_id: uuid.UUID, agent_id: uuid.UUID) -> dict[str, Any]:
    """
    Fetch full agent + permissions from Registry service.
    Source of Truth.
    """
    url = f"{policy_settings.REGISTRY_SERVICE_URL.rstrip('/')}/agents/{agent_id}"
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    headers = {
        "X-Internal-Secret": policy_settings.INTERNAL_SECRET,
        "X-Tenant-ID": str(tenant_id),
    }
    if request_id:
        headers["X-Request-ID"] = request_id

    try:
        client = get_registry_client()
        resp = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        logger.error("registry_unreachable", error=str(exc), reason="system_unavailable")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registry service unreachable: system_unavailable",
        ) from exc

    if resp.status_code == 404:
        # P-6 FIX: Handle Global Management Context (Agent 0)
        # Match Gateway virtual metadata for consistent policy evaluation
        if agent_id == uuid.UUID(int=0):
            return {
                "id": str(agent_id),
                "tenant_id": str(tenant_id),
                "name": "Global Management Context",
                "status": "active",
                "risk_level": "low",
                "permissions": [
                    {"tool_name": "*", "action": "allow", "granted_by": str(uuid.UUID(int=0))}
                ],
            }

        # Agent not found — return a minimal stub so OPA evaluates with zero permissions.
        # The OPA policy will deny the tool (no ALLOW grants) and return risk_adjustment=0.
        # This is safer than hard-failing: callers can still handle a policy deny gracefully.
        logger.warning("agent_not_found_policy_stub", agent_id=str(agent_id))
        return {
            "id": str(agent_id),
            "tenant_id": str(tenant_id),
            "name": "unknown",
            "status": "inactive",
            "risk_level": "low",
            "permissions": [],
        }

    if resp.status_code != 200:
        logger.error("registry_error", status_code=resp.status_code, reason="system_unavailable")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unexpected response from registry: system_unavailable",
        )

    json_resp = resp.json()
    return dict(json_resp.get("data", {}))


def _build_opa_input(
    agent: dict[str, Any],
    payload: EvaluationRequest
) -> dict[str, Any]:
    """
    Central OPA input builder.
    Ensures a consistent schema for all policy requests, including metadata.

    SPRINT enterprise-grade 2026-06-14: `metadata` is now forwarded to OPA
    so the action-semantics rules can read `input.metadata.arguments.{...}`.
    Before this, the slow path built opa_input without metadata, so every
    rule in action_semantics_deny.rego that keys off command_norm /
    query_norm / row_limit / k8s_namespace evaluated against empty inputs
    and never fired.
    """
    permissions = []
    for p in agent.get("permissions", []):
        permissions.append(
            {
                "tool_name": p["tool_name"],
                "action": p["action"],
                "granted_by": str(p["granted_by"]),
                "expires_at": p.get("expires_at"),
            }
        )

    return {
        "tenant_id": str(agent["tenant_id"]),
        "agent": {
            "id": str(agent["id"]),
            "name": agent["name"],
            "status": agent["status"],
            "risk_level": agent.get("risk_level", "low"),
            "permissions": permissions,
        },
        "tool": payload.tool,
        "risk_score": payload.risk_score,
        "behavior_history": payload.behavior_history,
        "policy_version": payload.policy_version,
        # Merge the inbound metadata with the request envelope. The earlier
        # version of this dict was buggy: it set "metadata" twice — the second
        # write silently clobbered the first, dropping request_id + timestamp
        # whenever payload.metadata was non-empty. Now one merged dict, with
        # the envelope fields winning over any caller-supplied collision.
        "metadata": {
            **(payload.metadata or {}),
            "request_id": payload.request_id or structlog.contextvars.get_contextvars().get("request_id"),
            "timestamp": payload.timestamp.isoformat(),
        },
    }


async def _log_audit(
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    tool: str,
    allowed: bool,
    reason: str,
    risk_score: float,
    risk_adjustment: float,
) -> None:
    """
    Send decision to Audit service via simple HTTP POST.
    """
    url = f"{policy_settings.AUDIT_SERVICE_URL.rstrip('/')}/audit/logs"
    request_id = structlog.contextvars.get_contextvars().get("request_id")

    payload = {
        "tenant_id": str(tenant_id),
        "agent_id": str(agent_id),
        "action": "policy_evaluation",
        "tool": tool,
        "decision": "allow" if allowed else "deny",
        "reason": reason,
        "request_id": request_id,
        "metadata_json": {
            "risk_score": risk_score,
            "risk_adjustment": risk_adjustment,
            "policy_version": "v1",
            "source": "policy_service"
        }
    }

    try:
        client = get_audit_client()
        await client.post(url, json=payload)
    except Exception as exc:
        logger.warning("audit_logging_failed", error=str(exc))


# =========================
# EVALUATE ENDPOINT
# =========================


@router.post(
    "/evaluate",
    response_model=APIResponse[EvaluationResponse],
    summary="Evaluate whether an agent may execute a tool",
)
async def evaluate(
    payload: EvaluationRequest,
    _: Annotated[bool, Depends(check_deadline)] = True,
    __: Annotated[str, Depends(verify_internal_secret)] = "",
) -> APIResponse[EvaluationResponse]:
    """
    Adaptive multi-layer policy evaluation:
    1. Fetches agent from Registry (Source of Truth)
    2. Builds consistent OPA input document with metadata
    3. Calls OPA and retrieves decision, reason, and RISK ADJUSTMENT
    4. Logs decision and adjustment to Audit Service
    5. Returns unified APIResponse
    """
    # ARCH-1/3/4 2026-06-15 — defaults so the caller always gets the
    # explainability fields filled even on the slow path or on allow.
    tier = "allow"
    findings: list[str] = []
    policy_id = ""
    risk_score_inherent = 0
    explanation = ""

    # Fast-path: gateway embedded JWT agent_claims → evaluate locally (< 1ms, no HTTP)
    # FUP-4 2026-06-15 — also splits the result into SEC + GOV slices so
    # the response carries engine attribution.
    sec_slice: dict[str, Any] = {"tier": "allow", "findings": [], "policy_id": "", "risk_score": 0}
    gov_slice: dict[str, Any] = {"tier": "allow", "findings": [], "policy_id": "", "risk_score": 0}
    if payload.agent_claims:
        from services.policy.local_eval import evaluate
        from services.policy.local_action_semantics import evaluate_full as eval_action_semantics_full
        from services.policy.security_engine import evaluate_security
        from services.policy.governance_engine import evaluate_governance
        agent_claims = payload.agent_claims
        risk_level = agent_claims.get("risk_level", "low")
        allowed, reason, risk_adjustment = evaluate(
            agent_status=str(agent_claims.get("agent_status") or agent_claims.get("status") or "active"),
            permissions=agent_claims.get("permissions", []),
            tool=payload.tool,
            risk_score=payload.risk_score,
            risk_level=risk_level,
        )
        # SPRINT enterprise-grade 2026-06-14 / ARCH-1 2026-06-15: action-
        # semantics now returns the full tier + findings + policy_id +
        # explanation bag. The fast path always queries it (even on allow)
        # so MONITOR-tier informational findings still surface in the
        # response — that's how the SOC sees "schema_recon happened" or
        # "compression observed" even when no block fired.
        if allowed:
            meta = payload.metadata or {}
            arguments = meta.get("arguments") if isinstance(meta, dict) else None
            full = eval_action_semantics_full(arguments, risk_level)
            tier = full.get("tier") or "allow"
            findings = list(full.get("findings") or [])
            policy_id = full.get("policy_id") or ""
            risk_score_inherent = int(full.get("risk_score") or 0)
            explanation = full.get("explanation") or ""
            # ARCH-8 / FUP-4: fan-out engine slices.
            try:
                sec_full = evaluate_security(arguments, risk_level)
                gov_full = evaluate_governance(arguments, risk_level)
                sec_slice = {
                    "tier":       sec_full.get("tier") or "allow",
                    "findings":   list(sec_full.get("findings") or []),
                    "policy_id":  sec_full.get("policy_id") or "",
                    "risk_score": int(sec_full.get("risk_score") or 0),
                }
                gov_slice = {
                    "tier":       gov_full.get("tier") or "allow",
                    "findings":   list(gov_full.get("findings") or []),
                    "policy_id":  gov_full.get("policy_id") or "",
                    "risk_score": int(gov_full.get("risk_score") or 0),
                }
            except Exception:
                pass
            if tier in ("deny", "quarantine"):
                allowed = False
                reason = policy_id or full.get("reason") or "policy_deny"
                risk_adjustment = 0.95 if tier == "quarantine" else 0.90
            elif tier == "escalate":
                allowed = False
                # __escalate suffix preserves the decision engine's
                # "approval_required vs hard deny" downstream branch.
                reason = (policy_id or full.get("reason") or "policy_escalate") + "__escalate"
                risk_adjustment = 0.80
            # tier in ("allow", "monitor") → keep allowed=True, surface findings.
    else:
        agent_dict = await _fetch_agent(payload.tenant_id, payload.agent_id)
        opa_input = _build_opa_input(agent_dict, payload)
        allowed, reason, risk_adjustment = await opa_client.check_policy(opa_input)
        # Slow path doesn't yet surface findings/tier — OPA returns just
        # (allowed, reason, risk). Derive a coarse tier from the suffix.
        if not allowed:
            tier = "escalate" if reason.endswith("__escalate") else "deny"
            findings = [reason.replace("__escalate", "")]
            policy_id = reason.replace("__escalate", "")
            risk_score_inherent = 90 if tier == "deny" else 50

    asyncio.create_task(_safe_bg(_log_audit(
        payload.tenant_id, payload.agent_id, payload.tool,
        allowed, reason, payload.risk_score, risk_adjustment
    )))

    if not allowed:
        logger.warning(
            "policy_denied", agent_id=str(payload.agent_id),
            tool=payload.tool, reason=reason, tier=tier,
            policy_id=policy_id, findings=findings,
        )

    # Sprint 1 2026-06-15 — MITRE tactic+technique is added GATEWAY-SIDE
    # (services/gateway/middleware.py) because the policy container today
    # mounts only services/policy/ — it doesn't have services/security
    # on disk. The gateway has the whole services tree, so the lookup
    # there is free. EvaluationResponse.mitre stays in the schema as a
    # zero-cost field (defaulted to {}) so contract is unchanged.
    return APIResponse(
        data=EvaluationResponse(
            agent_id=payload.agent_id,
            tool=payload.tool,
            allowed=allowed,
            reason=reason,
            risk_adjustment=risk_adjustment,
            evaluated_at=datetime.now(tz=UTC),
            tier=tier,
            findings=findings,
            policy_id=policy_id,
            risk_score=risk_score_inherent,
            explanation=explanation,
            security=sec_slice,
            governance=gov_slice,
        )
    )


# =========================
# POLICY SIMULATION (DRY-RUN)
# =========================

_TIME_RANGE_HOURS = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "==", "neq": "!="}


def _eval_condition(cond, log: dict) -> bool:
    meta = log.get("metadata_json") or {}
    field_map = {
        "risk_score":     float(meta.get("risk_score",     0)),
        "inference_risk": float(meta.get("inference_risk", 0)),
        "behavior_risk":  float(meta.get("behavior_risk",  0)),
        "anomaly_score":  float(meta.get("anomaly_score",  0)),
        "tool":           str(log.get("tool", "")),
    }
    actual = field_map.get(cond.field)
    if actual is None:
        return False
    try:
        val = str(cond.value)
        if cond.field != "tool":
            a, v = float(actual), float(val)
            _cmp = {"gt": a > v, "gte": a >= v, "lt": a < v, "lte": a <= v, "eq": a == v, "neq": a != v}
            return _cmp.get(cond.operator, False)
        ops = {"eq": actual == val, "neq": actual != val}
        return ops.get(cond.operator, False)
    except Exception:
        return False


def _simulate_decision(policy: list, log: dict) -> str:
    for rule in policy:
        if all(_eval_condition(c, log) for c in rule.conditions):
            return rule.action.lower()
    return "allow"


@router.post(
    "/simulate",
    response_model=APIResponse[SimulateResponse],
    summary="Dry-run a policy against historical audit events",
    dependencies=[Depends(verify_internal_secret)],
)
async def simulate_policy(payload: SimulateRequest) -> APIResponse[SimulateResponse]:
    """
    Fetch recent audit logs for the agent and replay them through the proposed
    policy rules locally. Returns a diff of decisions that would change.
    No OPA call, no writes — pure read + evaluate.
    """
    from datetime import datetime, timedelta

    hours     = _TIME_RANGE_HOURS.get(payload.time_range, 24)
    (datetime.now(UTC) - timedelta(hours=hours)).isoformat()

    try:
        client = get_audit_client()
        resp   = await client.get(
            f"{policy_settings.AUDIT_SERVICE_URL.rstrip('/')}/logs",
            params={"agent_id": str(payload.agent_id), "limit": 200, "offset": 0},
            headers={"X-Internal-Secret": policy_settings.INTERNAL_SECRET,
                     "X-Tenant-ID": str(payload.tenant_id) if payload.tenant_id else ""},
        )
        logs: list[dict] = resp.json().get("data", {}).get("items", []) if resp.status_code == 200 else []
    except Exception as exc:
        logger.warning("simulate_audit_fetch_failed", error=str(exc))
        logs = []

    diff:    list[SimulateDiffItem] = []
    allow_n  = deny_n = no_change = 0

    for log in logs:
        old_dec = (log.get("decision") or "allow").lower()
        new_dec = _simulate_decision(payload.policy, log)

        if new_dec in ("allow", "monitor"):
            allow_n += 1
        else:
            deny_n += 1

        if old_dec != new_dec:
            diff.append(SimulateDiffItem(
                event_id     = str(log.get("id", "")),
                tool         = str(log.get("tool", "unknown")),
                timestamp    = str(log.get("timestamp", "")),
                risk_score   = float((log.get("metadata_json") or {}).get("risk_score", 0)),
                old_decision = old_dec,
                new_decision = new_dec,
            ))
        else:
            no_change += 1

    return APIResponse(data=SimulateResponse(
        total_events = len(logs),
        would_allow  = allow_n,
        would_deny   = deny_n,
        no_change    = no_change,
        diff         = diff[:20],   # cap sample at 20 for payload size
    ))


@router.post(
    "/execute",
    summary="Execute a tool (security-controlled)",
    dependencies=[Depends(verify_internal_secret)],
)
async def execute_tool(
    request: Request,
    payload: dict[str, Any],
    _: Annotated[bool, Depends(check_deadline)] = True,
) -> dict[str, Any]:
    """
    Final tool execution destination.
    In this control plane, it records the execution and returns the decision context.
    The Gateway's SecurityMiddleware has already authorized this request.
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    agent_id = request.headers.get("X-Agent-ID", "")
    tenant_id = request.headers.get("X-Tenant-ID", "")
    tool = payload.get("tool") or request.headers.get("X-ACP-Tool", "unknown")

    # Extract decision metadata injected by the Gateway
    decision_meta = payload.get("_decision", {})

    # RULE: All executions are recorded for auditing and forensic replay
    # In a real system, this is where the actual agent tool invocation happens.

    return {
        "success": True,
        "request_id": request_id,
        "agent_id": agent_id,
        "tenant_id": tenant_id,
        "tool": tool,
        "action": decision_meta.get("action", "allow"),
        "risk": decision_meta.get("risk", 0.0),
        "confidence": decision_meta.get("confidence", 1.0),
        "findings": decision_meta.get("findings", []),
        "reasons": decision_meta.get("reasons", []),
        "signals": decision_meta.get("signals", {}),
        "executed_at": datetime.now(tz=UTC).isoformat(),
    }


# =========================
# POLICY TEST (DRY-RUN against live OPA)
# =========================

_ALLOWED_ROLES: frozenset[str] = frozenset({"ADMIN", "SECURITY"})
_NAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


def _require_admin_or_security(
    x_acp_role: str | None = Header(default=None),
    _secret: str = Depends(verify_internal_secret),
) -> str:
    """Require ADMIN or SECURITY role (injected by Gateway from validated JWT)."""
    role = (x_acp_role or "").upper()
    if role not in _ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ADMIN or SECURITY role required",
        )
    return role


class PolicyTestCase(BaseModel):
    tool_name: str
    parameters: dict[str, Any] = {}
    risk_score: float = 0.0
    expected: str  # "allow" or "deny"


class PolicyTestRequest(BaseModel):
    rego: str
    test_cases: list[PolicyTestCase]


class PolicyTestResult(BaseModel):
    input: dict[str, Any]
    expected: str
    actual: str
    passed: bool
    risk_adjustment: float


class PolicyTestResponse(BaseModel):
    results: list[PolicyTestResult]
    passed_count: int
    total: int
    all_passed: bool


@router.post(
    "/test",
    response_model=APIResponse[PolicyTestResponse],
    summary="Test Rego policy against sample inputs (dry-run, no auth required)",
    tags=["policy"],
)
async def test_policy(payload: PolicyTestRequest) -> APIResponse[PolicyTestResponse]:
    """
    Evaluate each test case against the live OPA policy engine using
    ``opa_client.check_policy()``. The ``rego`` field is shown for UX context
    (future: could be pushed as a temporary bundle), but evaluation uses the
    currently deployed OPA policy so admins can validate expected outcomes
    against real policy behaviour without activating any changes.

    No agent authentication required — this is an admin dry-run tool.
    """
    results: list[PolicyTestResult] = []

    for case in payload.test_cases:
        opa_input: dict[str, Any] = {
            "tool": case.tool_name,
            "risk_score": case.risk_score,
            "parameters": case.parameters,
            # Provide a minimal agent stub so OPA can evaluate without a real agent
            "agent": {
                "id": str(uuid.UUID(int=0)),
                "name": "policy_test_stub",
                "status": "active",
                "risk_level": "low",
                "permissions": [
                    {"tool_name": "*", "action": "allow", "granted_by": str(uuid.UUID(int=0))}
                ],
            },
            "behavior_history": [],
            "metadata": {"source": "policy_test"},
        }

        try:
            allowed, _reason, risk_adjustment = await opa_client.check_policy(opa_input)
        except Exception as exc:
            logger.warning("policy_test_opa_error", error=str(exc), tool=case.tool_name)
            allowed, risk_adjustment = False, 0.0

        actual = "allow" if allowed else "deny"
        expected = case.expected.lower()
        results.append(
            PolicyTestResult(
                input=opa_input,
                expected=expected,
                actual=actual,
                passed=(actual == expected),
                risk_adjustment=risk_adjustment,
            )
        )

    passed_count = sum(1 for r in results if r.passed)
    return APIResponse(
        data=PolicyTestResponse(
            results=results,
            passed_count=passed_count,
            total=len(results),
            all_passed=(passed_count == len(results)),
        )
    )


# =========================
# POLICY UPLOAD
# =========================


class PolicyUploadRequest(BaseModel):
    name: str
    rego: str
    description: str = ""


class PolicyUploadResponse(BaseModel):
    status: str
    path: str
    reload_hint: str


@router.post(
    "/upload",
    response_model=APIResponse[PolicyUploadResponse],
    summary="Save a named Rego policy to disk (ADMIN/SECURITY only)",
    tags=["policy"],
)
async def upload_policy(
    payload: PolicyUploadRequest,
    tenant_id: Annotated[uuid.UUID, Depends(get_tenant_id)],
    _role: str = Depends(_require_admin_or_security),
) -> APIResponse[PolicyUploadResponse]:
    """
    Validate ``name``, write the Rego content to
    ``/tmp/acp_policies/{tenant_id}/{name}.rego``, and return a reload hint.
    OPA polls the bundle server every 30 s so the new policy takes effect
    automatically without a manual reload.

    Requires ADMIN or SECURITY role (enforced via ``X-ACP-Role`` header injected
    by the Gateway from the validated JWT claims).
    """
    if not _NAME_RE.match(payload.name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Policy name must match ^[a-zA-Z0-9_]{1,64}$",
        )

    policy_dir = os.path.join("/tmp", "acp_policies", str(tenant_id))
    os.makedirs(policy_dir, exist_ok=True)

    file_path = os.path.join(policy_dir, f"{payload.name}.rego")
    try:
        with open(file_path, "w", encoding="utf-8") as fh:
            fh.write(payload.rego)
    except OSError as exc:
        logger.error("policy_upload_write_failed", path=file_path, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to write policy file: {exc}",
        ) from exc

    logger.info(
        "policy_uploaded",
        tenant_id=str(tenant_id),
        name=payload.name,
        path=file_path,
        description=payload.description,
    )

    return APIResponse(
        data=PolicyUploadResponse(
            status="saved",
            path=file_path,
            reload_hint="OPA polls bundle server every 30s",
        )
    )


# =========================
# HEALTH
# =========================


@router.get("/health/opa", tags=["ops"], summary="Check OPA connectivity")
async def opa_health() -> dict[str, str]:
    healthy = await opa_client.health()
    if not healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OPA is not reachable",
        )
    return {"opa": "ok"}
