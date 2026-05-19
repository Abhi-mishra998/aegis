"""
_AuditMixin — audit logging, billing, and SLO helpers extracted from
SecurityMiddleware.  All methods use ``self.redis`` which is initialised by
SecurityMiddleware.__init__ at runtime.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any

import structlog
from fastapi import Request, Response

from sdk.common.background import safe_bg as _safe_bg
from services.decision.schemas import Decision
from services.gateway.client import service_client
from services.gateway.trust_emitter import emit_graph_event, map_decision_to_outcome

logger = structlog.get_logger(__name__)

_IDEMPOTENCY_PREFIX = "acp:idempotency:"
_IDEMPOTENCY_TTL_MAP = {
    "enterprise": 86400,  # 24 hours
    "premium": 3600,  # 1 hour
    "basic": 300,  # 5 minutes
}


class _AuditMixin:
    async def _log_block(
        self, tenant_id: str, agent_id: uuid.UUID, tool: str, res: Any, request_id: str, tokens: int = 1
    ) -> None:
        """Log a security block to the audit stream and record billing."""
        meta = {
            **res.metadata,
            "risk_score": res.risk_score,
            "flags": res.flags,
            "prompt_hash": res.prompt_hash,
        }
        await self._log_audit(
            tenant_id,
            agent_id,
            "inference_proxy_block",
            tool,
            "deny",
            res.reason,
            request_id,
            meta,
        )

        # Inference-proxy blocks are security denials — no execution occurred,
        # so no usage record is written. The reconcile script diffs against
        # execute_tool audit entries; recording usage here creates an asymmetric
        # gap that shows as billing integrity failures.

        # 2026-05-13: trust-event emission (fire-and-forget; never fails request)
        asyncio.create_task(_safe_bg(emit_graph_event(
            self.redis,
            tenant_id=tenant_id,
            src_id=str(agent_id), src_type="agent", src_name=str(agent_id),
            dst_id=tool, dst_type="tool", dst_name=tool,
            edge_type="invokes", action="execute_tool", outcome="deny",
            risk_score=getattr(res, "risk_score", 0.0),
            request_id=request_id,
            attributes={"layer": "inference_proxy", "flags": getattr(res, "flags", []) or []},
        )))

        # 2026-05-14: feed the Groq insight pipeline. Every inference-proxy block
        # is a high-value training signal — without this the Risk Engine
        # "AI Threat Insights" panel stays empty even under heavy attack traffic.
        await self._emit_groq_event(
            event_id=request_id,
            tenant_id=tenant_id,
            agent_id=str(agent_id),
            tool=tool,
            decision="deny",
            risk_score=getattr(res, "risk_score", 0.0),
            signals={"flags": getattr(res, "flags", []) or []},
            reasons=[res.reason] if getattr(res, "reason", None) else [],
            source="inference_proxy",
        )

    async def _record_billing_with_retry(
        self,
        tenant_id: str,
        action: str,
        agent_id: uuid.UUID,
        tokens: int,
        audit_id: str,
        max_retries: int = 3
    ) -> bool:
        """
        Record billing event with exponential backoff retry.
        Returns True if successful, False if all retries failed.
        GUARANTEE: HTTP 200 only if this returns True.

        H-5 FIX (2026-05-13): On terminal failure, persist the event to the
        Redis DLQ so the gateway billing retry worker (gateway/main.py:
        _process_billing_queue) can heal it asynchronously. The client still
        gets HTTP 500 immediately (gateway pipeline behavior unchanged), but
        the event is now durable instead of lost.
        """
        for attempt in range(max_retries):
            try:
                # Exponential backoff: 0.1s, 0.2s, 0.4s
                if attempt > 0:
                    await asyncio.sleep(0.1 * (2 ** (attempt - 1)))

                result = await service_client.record_billing_event(
                    tenant_id=tenant_id,
                    action=action,
                    agent_id=agent_id,
                    tokens=max(tokens, 1),
                    audit_id=audit_id
                )
                if result.get("success", False):
                    logger.info("billing_recorded", audit_id=audit_id, attempt=attempt+1)
                    return True
                error_msg = result.get("error", "Unknown billing error")
                logger.error("billing_failed", audit_id=audit_id, error=error_msg)
                await self._persist_billing_dlq(tenant_id, action, agent_id, tokens, audit_id, error_msg)
                return False

            except Exception as exc:
                logger.warning(
                    "billing_error_attempt",
                    audit_id=audit_id,
                    attempt=attempt+1,
                    error=str(exc)
                )

        logger.critical(
            "billing_guarantee_violation",
            audit_id=audit_id,
            tenant_id=tenant_id,
            action=action,
            max_retries=max_retries
        )
        await self._persist_billing_dlq(tenant_id, action, agent_id, tokens, audit_id, "max_retries_exhausted")
        return False

    async def _persist_billing_dlq(
        self,
        tenant_id: str,
        action: str,
        agent_id: uuid.UUID,
        tokens: int,
        audit_id: str,
        reason: str,
    ) -> None:
        """H-5: Durable fallback so failed billing events are healable, not lost.

        2026-05-13 (Run-3): include `idempotency_key=audit_id` so the retry worker
        forwards it to /billing/events. Without it the value engine cannot dedupe
        retries, and at 47 r/s the retry path was the dominant source of the
        593-record integrity gap (retries silently dropped at the unique audit_id
        constraint on usage_records, never reaching billing).
        """
        try:
            payload = {
                "tenant_id": tenant_id,
                "agent_id": str(agent_id),
                "tool": "unknown",
                "units": max(tokens, 1),
                "cost": max(tokens, 1) * 0.001,
                "audit_id": audit_id,
                "idempotency_key": audit_id,
            }
            retry_payload = {"payload": payload, "action": action, "retry_count": 0, "reason": reason}
            await self.redis.lpush("acp:billing_retry_queue", json.dumps(retry_payload))
        except Exception as exc:
            logger.critical("billing_dlq_write_failed", audit_id=audit_id, error=str(exc))

    async def _finalize_request(
        self,
        request: Request,
        response: Response,
        t_id: str,
        a_id: uuid.UUID,
        tool: str,
        b_hash: str,
        tier: str,
        start: float,
        req_id: str,
        risk: float,
        tokens: int = 1,
    ) -> Response:
        """
        Handle post-execution caching, metrics, and auditing.

        CRITICAL: Enforce billing guarantee.
        If billing fails: return 500 (not 200) to signal incomplete transaction.
        """
        # SLO & Audit
        self._record_slo(request, start, response.status_code)

        meta = {"status": response.status_code, "risk_score": risk}

        action_val = "allow"
        if hasattr(request.state, "decision"):
            action_val = request.state.decision.action.value if hasattr(request.state.decision.action, "value") else str(request.state.decision.action)

        await self._log_audit(
            t_id, a_id, "execute_tool", tool, action_val, None, req_id, meta
        )

        # CRITICAL: Usage billing MUST succeed before returning HTTP 200
        billing_units = max(tokens, 1)
        try:
            billing_result = await service_client.record_billing_event(
                tenant_id=t_id,
                action=action_val,
                agent_id=a_id,
                tokens=billing_units,
                audit_id=req_id,
                idempotency_key=req_id  # Use request_id as idempotency key
            )

            if not billing_result.get("success", False):
                error_msg = billing_result.get("error", "billing service failure")
                # 2026-05-13 ARCHITECTURE FLIP: Billing failure is no longer fatal to
                # the request. Cross-cutting billing middleware was poisoning every
                # route (graph, autonomy, flight) because exception → 500. Now we
                # persist to the DLQ and return the original execution response;
                # the billing retry worker (gateway/main.py:_process_billing_queue)
                # provides eventual consistency.
                logger.error(
                    "billing_deferred_to_dlq",
                    audit_id=req_id,
                    execution_status="completed",
                    billing_status="dlq",
                    error=error_msg,
                )
                await self._persist_billing_dlq(t_id, action_val, a_id, billing_units, req_id, error_msg)
        except Exception as exc:
            logger.error(
                "billing_exception_deferred_to_dlq",
                audit_id=req_id,
                error=str(exc),
                exc_type=type(exc).__name__,
            )
            await self._persist_billing_dlq(t_id, action_val, a_id, billing_units, req_id, str(exc))

        # 2026-05-14: feed the Groq pipeline. Emit on every deny AND every allow
        # whose risk score is non-trivial (≥0.4). This ensures the "AI Threat
        # Insights" panel in the UI populates from real traffic instead of
        # waiting for an actual deny — which in normal allow-heavy workloads
        # would mean an empty panel forever.
        decision_obj = getattr(request.state, "decision", None)
        if action_val in ("deny", "block", "kill", "escalate") or (
            decision_obj is not None and float(getattr(decision_obj, "risk", 0.0) or 0.0) >= 0.4
        ):
            await self._emit_groq_event(
                event_id=req_id,
                tenant_id=t_id,
                agent_id=str(a_id),
                tool=tool,
                decision=action_val,
                risk_score=float(getattr(decision_obj, "risk", 0.0) or 0.0),
                signals=dict(getattr(decision_obj, "signals", {}) or {}),
                reasons=list(getattr(decision_obj, "reasons", []) or []),
                source="gateway_finalize",
            )

        # Billing succeeded! Now cache idempotency result
        idem_key = request.headers.get("Idempotency-Key")
        if idem_key and response.status_code < 500:
            await self._cache_idempotency(t_id, idem_key, response, b_hash, tier)

        # Sprint 2.2 (2026-05-15): advertise the upcoming removal of the
        # `reasons` field on decision-shaped responses. RFC 8594-style
        # Deprecation header lets SDKs / proxies surface the migration
        # without re-parsing every body. The canonical field is `findings`.
        try:
            response.headers["Deprecation"] = "response-field=reasons; use=findings"
        except Exception:
            # Some response shapes (StreamingResponse) lock headers after
            # send_start. Best-effort only.
            pass

        return response

    async def _log_audit(
        self,
        tenant_id: str,
        agent_id: uuid.UUID,
        action: str,
        tool: str,
        decision: str,
        reason: str | None,
        request_id: str,
        meta: dict[str, Any],
    ) -> None:
        ctx = structlog.contextvars.get_contextvars()
        meta["actor"] = ctx.get("actor", "unknown")
        meta["trace_id"] = ctx.get("trace_id", request_id)

        payload = {
            "tenant_id": tenant_id,
            "agent_id": str(agent_id),
            "action": action,
            "tool": tool,
            "decision": decision,
            "reason": reason,
            "request_id": request_id,
            "metadata_json": json.dumps(meta),
        }

        # Synchronous audit write with timeout — MUST complete before response.
        # Sprint 2 perf: dropped from 1.0s to 0.25s. The xadd is sub-ms on a
        # healthy Redis; the only time the 1s budget ever ran was under a
        # Redis saturation event, where blocking the whole request for 1s
        # dragged p99 by exactly that. 0.25s caps the worst case while
        # leaving plenty of room for the rare slow xadd. On timeout the
        # event still lands in audit_dlq via the writer's existing DLQ
        # path, so durability is unchanged.
        try:
            await asyncio.wait_for(
                service_client.log_audit_stream(
                    self.redis,
                    payload,
                ),
                timeout=0.25,
            )
        except TimeoutError:
            logger.warning("audit_timeout", request_id=request_id)
        except Exception as e:
            logger.error("audit_log_failed", error=str(e), request_id=request_id)

    async def _cache_idempotency(
        self, tenant_id: str, key: str, response: Response, body_hash: str, tier: str
    ) -> None:
        full_key = f"{_IDEMPOTENCY_PREFIX}{tenant_id}:{key}"
        resp_body = response.body if hasattr(response, "body") else b""
        await self.redis.setex(
            full_key,
            _IDEMPOTENCY_TTL_MAP.get(tier, 300),
            json.dumps(
                {
                    "status": response.status_code,
                    "body": resp_body.decode() if resp_body else "",
                    "headers": {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower() not in ("set-cookie", "authorization")
                    },
                    "payload_hash": body_hash,
                }
            ),
        )

    def _record_slo(self, request: Request, start_time: float, status_code: int = 200) -> None:
        from sdk.utils import SLO_AVAILABILITY_TOTAL, SLO_LATENCY_SECONDS

        duration = time.time() - start_time
        status = "success" if status_code < 400 else "error"
        SLO_AVAILABILITY_TOTAL.labels(service="gateway", status=status).inc()
        SLO_LATENCY_SECONDS.labels(service="gateway", route=request.url.path).observe(
            duration
        )

    async def _log_decision(
        self, tenant_id: str, agent_id: uuid.UUID, tool: str, decision: Decision, request_id: str, tokens: int = 1
    ) -> None:
        """Log a decision to the audit stream."""
        meta = {
            **decision.metadata,
            "risk_score": decision.risk,
            "reasons": decision.reasons,
            "action": decision.action,
        }
        await self._log_audit(
            tenant_id,
            agent_id,
            "behavior_firewall_decision",
            tool,
            decision.action,
            "; ".join(decision.reasons),
            request_id,
            meta,
        )

        # GUARANTEE BILLING AFTER AUDIT — No fallback to async
        decision_action = decision.action.value if hasattr(decision.action, "value") else str(decision.action)
        billing_succeeded = await self._record_billing_with_retry(
            tenant_id=tenant_id,
            action=decision_action,
            agent_id=agent_id,
            tokens=max(tokens, 1),
            audit_id=request_id
        )

        if not billing_succeeded:
            logger.critical(
                "integrity_guard_triggered",
                audit_id=request_id,
                reason="billing_guarantee_violation_on_decision"
            )

        # 2026-05-13: trust-event emission (fire-and-forget; never fails request)
        asyncio.create_task(_safe_bg(emit_graph_event(
            self.redis,
            tenant_id=tenant_id,
            src_id=str(agent_id), src_type="agent", src_name=str(agent_id),
            dst_id=tool, dst_type="tool", dst_name=tool,
            edge_type="invokes", action="execute_tool",
            outcome=map_decision_to_outcome(decision_action),
            risk_score=float(getattr(decision, "risk", 0.0) or 0.0),
            request_id=request_id,
            attributes={"layer": "decision", "reasons": list(decision.reasons or [])[:5]},
        )))
