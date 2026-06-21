"""
ACP Gateway — Trust event emitter.

Single point of integration for the next-gen runtime trust platform:

  emit_graph_event(...)       → acp:graph_events       (identity_graph service)
  emit_flight_event(...)      → acp:flight_events      (flight_recorder service)
  check_autonomy_contract(..) → calls autonomy service /autonomy/check

All emitters are FIRE-AND-FORGET — emitting trust signals must NEVER fail the
hot path. The audit + billing guarantees from the previous sprints are
untouched; trust signals are best-effort observability streams with their
own DLQs handled by their respective worker services.
"""
from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from typing import Any

import httpx
import structlog

from sdk.common.config import settings
from sdk.common.auth import mesh_headers

logger = structlog.get_logger(__name__)

GRAPH_STREAM_KEY  = "acp:graph_events"
FLIGHT_STREAM_KEY = "acp:flight_events"

GRAPH_STREAM_MAXLEN  = int(os.getenv("GRAPH_STREAM_MAXLEN", "200000"))
FLIGHT_STREAM_MAXLEN = int(os.getenv("FLIGHT_STREAM_MAXLEN", "500000"))

# Autonomy service URL (separate microservice; defaults to common port)
AUTONOMY_SERVICE_URL = os.getenv("AUTONOMY_SERVICE_URL", "http://autonomy:8000")

_autonomy_client: httpx.AsyncClient | None = None


async def _autonomy_get_client() -> httpx.AsyncClient:
    global _autonomy_client
    if _autonomy_client is None or _autonomy_client.is_closed:
        _autonomy_client = httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=0.5))
    return _autonomy_client


async def close() -> None:
    global _autonomy_client
    if _autonomy_client is not None and not _autonomy_client.is_closed:
        await _autonomy_client.aclose()
    _autonomy_client = None


# ---------------------------------------------------------------------------
# Identity Graph event
# ---------------------------------------------------------------------------
async def emit_graph_event(
    redis: Any,
    *,
    tenant_id: str | uuid.UUID,
    src_id: str,
    src_type: str = "agent",
    src_name: str | None = None,
    src_role: str | None = None,
    dst_id: str,
    dst_type: str = "tool",
    dst_name: str | None = None,
    edge_type: str = "invokes",
    action: str = "execute_tool",
    outcome: str = "allow",
    risk_score: float = 0.0,
    request_id: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Publish one identity-graph edge. Never raises."""
    try:
        payload = {
            "tenant_id": str(tenant_id),
            "src_id":    str(src_id),
            "src_type":  src_type,
            "src_name":  src_name or str(src_id),
            "src_role":  src_role,
            "dst_id":    str(dst_id),
            "dst_type":  dst_type,
            "dst_name":  dst_name or str(dst_id),
            "edge_type": edge_type,
            "action":    action,
            "outcome":   outcome,
            "risk_score": float(risk_score),
            "request_id": request_id,
            "attributes": attributes or {},
            "ts":        int(time.time()),
        }
        await redis.xadd(
            GRAPH_STREAM_KEY,
            {"data": json.dumps(payload, default=str)},
            maxlen=GRAPH_STREAM_MAXLEN, approximate=True,
        )
    except Exception as exc:
        logger.warning("graph_emit_failed", error=str(exc), request_id=request_id)


# ---------------------------------------------------------------------------
# Flight recorder events
# ---------------------------------------------------------------------------
async def emit_flight_event(redis: Any, ev: dict[str, Any]) -> None:
    """Generic flight-event emitter. ev must include `kind` and request_id."""
    try:
        await redis.xadd(
            FLIGHT_STREAM_KEY,
            {"data": json.dumps(ev, default=str)},
            maxlen=FLIGHT_STREAM_MAXLEN, approximate=True,
        )
    except Exception as exc:
        logger.warning("flight_emit_failed", error=str(exc), kind=ev.get("kind"))


async def emit_timeline_start(
    redis: Any, *, tenant_id: str, request_id: str,
    agent_id: str | None, tool: str | None, metadata: dict | None = None,
    session_id: str | None = None,
) -> None:
    """Sprint 3.5 — ``session_id`` is propagated from the gateway's
    ``X-Session-ID`` request header so the Session Explorer can group
    consecutive ``/execute`` calls into one conversation. Pre-Sprint-3
    callers omit the kwarg and timelines land with ``session_id=NULL``."""
    await emit_flight_event(redis, {
        "kind": "timeline_start", "tenant_id": tenant_id, "request_id": request_id,
        "agent_id": agent_id, "tool": tool, "metadata": metadata or {},
        "session_id": session_id,
    })
    # Producer-side SLI: every open MUST be paired with a close. The delta is
    # the operator's primary signal for leaked timelines.
    try:
        from sdk.utils import FLIGHT_TIMELINE_OPEN_TOTAL
        FLIGHT_TIMELINE_OPEN_TOTAL.inc()
    except ImportError:
        pass


async def emit_step(
    redis: Any, *, tenant_id: str, request_id: str, step_index: int,
    step_type: str, summary: str | None = None, payload: dict | None = None,
    latency_ms: int | None = None, risk_score: float | None = None,
    status: str = "ok",
) -> None:
    await emit_flight_event(redis, {
        "kind": "step", "tenant_id": tenant_id, "request_id": request_id,
        "step_index": step_index, "step_type": step_type, "status": status,
        "latency_ms": latency_ms, "risk_score": risk_score,
        "summary": summary, "payload": payload or {},
    })


async def emit_timeline_end(
    redis: Any, *, tenant_id: str, request_id: str,
    final_decision: str | None, final_risk: float | None, status: str = "ok",
) -> None:
    await emit_flight_event(redis, {
        "kind": "timeline_end", "tenant_id": tenant_id, "request_id": request_id,
        "final_decision": final_decision, "final_risk": final_risk, "status": status,
    })
    try:
        from sdk.utils import (
            FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL,
            FLIGHT_TIMELINE_CLOSED_TOTAL,
        )
        FLIGHT_TIMELINE_CLOSED_TOTAL.inc()
        # Bucket arbitrary upstream statuses into the SLI's 2 buckets so the
        # series cardinality stays bounded. Anything non-`ok` is `failed`.
        FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL.labels(
            status="ok" if status == "ok" else "failed",
        ).inc()
    except ImportError:
        pass


async def emit_snapshot(
    redis: Any, *, tenant_id: str, request_id: str, step_index: int,
    snapshot: dict | None = None,
    tokens_in: int | None = None, tokens_out: int | None = None,
) -> None:
    """Capture an opaque state snapshot at a specific execution checkpoint.

    The flight_recorder worker writes these to the `execution_snapshots` table
    so forensic replay can show the precise state at allow/deny/error
    transitions. Fire-and-forget — never raises.
    """
    await emit_flight_event(redis, {
        "kind": "snapshot",
        "tenant_id": tenant_id,
        "request_id": request_id,
        "step_index": step_index,
        "snapshot": snapshot or {},
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    })


# ---------------------------------------------------------------------------
# Autonomy contract check (synchronous — runs before /execute)
# ---------------------------------------------------------------------------
async def check_autonomy_contract(
    *,
    tenant_id: str,
    agent_id: str,
    action: str,
    request_id: str | None = None,
    cost_estimate_usd: float | None = None,
    runtime_estimate_seconds: int | None = None,
    tool_calls_so_far: int | None = None,
    timeout_s: float = 0.4,
    redis: Any | None = None,
) -> dict[str, Any]:
    """
    POST /autonomy/check. Returns the CheckResult shape:
       {"allowed": bool, "requires_approval": bool, "violated_rules": [...],
        "contract_id": ..., "reason": ...}

    FAIL-OPEN policy: if the autonomy service is unreachable or times out, we
    *do not* block the request — autonomy is an additive enforcement layer on
    top of OPA + Decision. Outages are logged + surfaced in /system/health.
    The gateway's existing Policy & Decision pipeline retains fail-closed
    semantics so a brownout never silently widens the trust boundary.

    2026-05-13 PERF: cache the "no enforceable contract" answer in Redis for
    60 s per (tenant, agent, action). This eliminates the HTTP round-trip on
    the 99% of requests that hit agents with no autonomy contract installed.
    """
    # 60s TTL: short enough that contract installs are visible quickly,
    # long enough to cut the autonomy round-trip on the hot path.
    cache_key = f"acp:autonomy_check:{tenant_id}:{agent_id}:{action}"
    if redis is not None:
        try:
            cached = await redis.get(cache_key)
            if cached is not None:
                # Bytes vs str depending on decode_responses config
                if isinstance(cached, (bytes, bytearray)):
                    cached = cached.decode()
                if cached == "ok":
                    return {"allowed": True, "requires_approval": False,
                            "violated_rules": [], "reason": "cached_no_contract"}
        except Exception:
            pass  # cache lookup failed → fall through to HTTP

    try:
        client = await _autonomy_get_client()
        resp = await client.post(
            f"{AUTONOMY_SERVICE_URL.rstrip('/')}/autonomy/check",
            headers={
                **mesh_headers("gateway"),
                "X-Tenant-ID": str(tenant_id),
                "Content-Type": "application/json",
            },
            json={
                "agent_id": str(agent_id),
                "action": action,
                "request_id": request_id,
                "cost_estimate_usd": cost_estimate_usd,
                "runtime_estimate_seconds": runtime_estimate_seconds,
                "tool_calls_so_far": tool_calls_so_far,
            },
            timeout=timeout_s,
        )
        if resp.status_code != 200:
            logger.warning("autonomy_check_non_200", status=resp.status_code)
            return {"allowed": True, "requires_approval": False, "violated_rules": [], "reason": "autonomy_degraded"}
        result = resp.json().get("data") or {"allowed": True, "requires_approval": False}
        # Cache the "no contract" decision so the next call skips the round-trip.
        if (
            redis is not None
            and result.get("allowed", True)
            and not result.get("requires_approval")
            and result.get("reason") == "no_contract"
        ):
            with contextlib.suppress(Exception):
                await redis.setex(cache_key, 60, "ok")
        return result
    except Exception as exc:
        logger.warning("autonomy_check_unreachable", error=str(exc), request_id=request_id)
        return {"allowed": True, "requires_approval": False, "violated_rules": [], "reason": "autonomy_unreachable"}


def map_decision_to_outcome(action: str) -> str:
    """Project decision-engine actions to graph edge outcomes."""
    a = (action or "").lower()
    if a in ("allow", "monitor"):
        return "allow"
    if a in ("deny", "block", "kill", "escalate"):
        return "deny"
    if a in ("throttle",):
        return "deny"
    return "error"
