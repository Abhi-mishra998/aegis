"""
_ResponseMixin — response builder helpers extracted from SecurityMiddleware.
All methods use ``self.redis`` which is initialised by SecurityMiddleware.__init__
at runtime.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog
from fastapi import Response
from fastapi.responses import JSONResponse, StreamingResponse

from sdk.common.background import safe_bg as _safe_bg
from services.gateway.inference_proxy import OutputFilter, inference_proxy
from services.gateway._helpers import publish_event

logger = structlog.get_logger(__name__)


class _ResponseMixin:
    # Sprint 2.3 — closes audit C22's "streaming + >256KB bypass" finding.
    # The buffered redaction path used to hold the entire body in memory
    # and skip outright when the payload was streamed or larger than 256
    # KB. The chunked path below redacts on the fly with a bounded tail
    # overlap so an unbounded LLM completion can be redacted line by line
    # without OOMing the worker, and an SSE stream gets the same regex
    # coverage as a buffered response.
    _MAX_BUFFERED_REDACT_BYTES = 256 * 1024  # 256 KB — buffered path ceiling
    _STREAM_CHUNK_BYTES = 16 * 1024          # streaming-path emit granularity

    async def _filter_response(self, response: Response) -> Response:
        content_type = response.headers.get("content-type", "")
        # Non-text content (binary, octet-stream): pass through — applying
        # text regexes to a JPEG would mangle it without finding secrets.
        if not any(t in content_type for t in ("json", "text", "xml", "event-stream")):
            return response

        is_streaming = (
            isinstance(response, StreamingResponse)
            or "event-stream" in content_type
            or "stream" in content_type
        )

        # If Content-Length advertises a payload above the buffer cap OR
        # the response is a stream, redact via the chunked path.
        try:
            advertised = int(response.headers.get("content-length", "0"))
        except ValueError:
            advertised = 0

        if is_streaming or (advertised and advertised > self._MAX_BUFFERED_REDACT_BYTES):
            return await self._filter_response_chunked(response)

        # Small, non-streaming response — keep the existing buffered path
        # (lower latency, single regex pass over the whole body).
        try:
            body = bytearray()
            async for chunk in response.body_iterator:
                body.extend(chunk if isinstance(chunk, bytes) else chunk.encode())
                if len(body) > self._MAX_BUFFERED_REDACT_BYTES:
                    # Mid-flight overflow → fall over to the chunked path
                    # rather than emitting the unredacted tail.
                    return await self._filter_response_chunked(
                        response, prebuffered=bytes(body),
                    )
            filtered = inference_proxy.filter_output(bytes(body))
            return Response(
                content=filtered,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        except Exception as exc:
            logger.critical("output_redaction_error", error=str(exc))
            return self._deny("Internal Security Error", 500)

    async def _filter_response_chunked(
        self,
        response: Response,
        *,
        prebuffered: bytes | None = None,
    ) -> StreamingResponse:
        """Redact a streaming or oversize response without buffering it.

        Yields redacted chunks via :meth:`OutputFilter.redact_chunked`. The
        per-chunk tail overlap keeps boundary-straddling secrets matchable.
        ``content-length`` is stripped (it would be wrong after redaction).
        """

        async def _aggregate_chunks() -> AsyncIterator[bytes]:
            # Re-emit whatever the buffered path had already collected
            # before deciding to switch to streaming.
            if prebuffered:
                yield prebuffered
            async for chunk in response.body_iterator:
                yield chunk if isinstance(chunk, bytes) else chunk.encode()

        async def _redacted_async() -> AsyncIterator[bytes]:
            buffer: list[bytes] = []
            buffered_bytes = 0
            try:
                async for chunk in _aggregate_chunks():
                    buffer.append(chunk)
                    buffered_bytes += len(chunk)
                    if buffered_bytes >= self._STREAM_CHUNK_BYTES:
                        for emit in OutputFilter.redact_chunked(buffer):
                            yield emit
                        buffer = []
                        buffered_bytes = 0
                if buffer:
                    for emit in OutputFilter.redact_chunked(buffer):
                        yield emit
            except Exception as exc:
                logger.critical("output_redaction_stream_error", error=str(exc))
                # Surface a sentinel so the SSE/JSON consumer sees a
                # truncation rather than a silent partial payload.
                yield b'{"error":"stream_redaction_failed"}'

        headers = {
            k: v for k, v in response.headers.items()
            if k.lower() != "content-length"
        }
        return StreamingResponse(
            _redacted_async(),
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    def _deny(self, message: str, status_code: int, *,
              findings: list[str] | None = None,
              reason: str | None = None,
              policy_id: str | None = None,
              risk_score: int | None = None,
              explanation: str | None = None,
              security: dict | None = None,
              governance: dict | None = None,
              mitre: dict | None = None) -> JSONResponse:
        ctx = structlog.contextvars.get_contextvars()

        logger.warning("security_rejection", **{
            "severity": "HIGH",
            "message": message,
            "status_code": status_code,
            "trace_id": ctx.get("trace_id", "unknown"),
            "tenant_id": ctx.get("tenant_id", "unknown"),
            "agent_id": ctx.get("agent_id", "unknown"),
            "confidence": 0.99
        })

        if status_code in (401, 403, 429):
            asyncio.create_task(_safe_bg(self.redis.incr("acp:metrics:blocked_requests")))

            t_id = ctx.get("tenant_id")
            c_ip = ctx.get("client_ip", "unknown")
            u_ag = ctx.get("user_agent", "unknown")
            a_id = ctx.get("agent_id", "unknown")

            # Metrics bounds implicitly fail-open being spawned as background Tasks without `try-except`.

            if t_id and t_id != "unknown":
                self._process_autonomous_abuse(str(t_id), str(c_ip), str(u_ag))

            # Sprint B follow-up 2026-06-14 — runaway-loop counter at the
            # single chokepoint every gateway-side deny passes through.
            # The prior in-dispatch hook missed escalates that returned
            # via `return self._deny(...)` (autonomy, decision short-
            # circuit, policy approval-required) because they never raised
            # an HTTPException. _deny() runs for ALL of them.
            if t_id and t_id != "unknown" and a_id and a_id != "unknown":
                tool = ctx.get("tool") or "unknown_tool"
                # GAP-4: pass reason/policy_id so the bulk-PII tighter
                # counter can fire based on the actual rule that matched.
                _deny_reason = (reason or policy_id or "") or ""
                asyncio.create_task(_safe_bg(
                    self._record_runaway_failure(str(t_id), str(a_id), str(tool), _deny_reason)
                ))

            # Real-time UI feed — pre-policy hard-denies (path traversal,
            # SQL injection, dangerous code, PII exfil) AND main-pipeline
            # denies + autonomy refusals all route through this single
            # chokepoint. Publishing here means the Live Feed shows the
            # block within ~100 ms of the operator/agent attempt — the
            # round-2 SSE coverage that the `policy_decision` event
            # promises to operators.
            if t_id and t_id != "unknown":
                _sse_tool = ctx.get("tool") or "unknown_tool"
                _sse_payload = {
                    "decision":   "deny",
                    "request_id": ctx.get("request_id"),
                    "agent_id":   str(a_id) if a_id and a_id != "unknown" else None,
                    "tool":       str(_sse_tool),
                    "status_code": status_code,
                }
                if findings:
                    _sse_payload["findings"] = list(findings)[:5]
                if reason:
                    _sse_payload["reason"] = reason
                if policy_id:
                    _sse_payload["policy_id"] = policy_id
                if risk_score is not None:
                    _sse_payload["risk_score"] = int(risk_score)
                asyncio.create_task(_safe_bg(
                    publish_event(self.redis, str(t_id), "policy_decision", _sse_payload)
                ))

        # 2026-06-15 — surface findings + raw reason in body so buyer SDKs
        # don't have to grep the `error` string. Previously the response
        # said `error: "Security Block: dropped_table"` and the SDK had to
        # regex the rule name out of it; now the rule name is a structured
        # field the SDK can read with `body['findings']`.
        body: dict[str, Any] = {
            "success": False,
            "error":   message,
            "meta":    {"code": status_code},
        }
        if findings:
            body["findings"] = list(findings)
        if reason:
            body["reason"] = reason
        # ARCH-4 2026-06-15 — explainability surface.
        if policy_id:
            body["policy_id"] = policy_id
        if risk_score is not None:
            body["risk_score"] = int(risk_score)
        if explanation:
            body["explanation"] = explanation
        # FUP-4 2026-06-15 — engine slices.
        if security:
            body["security"] = security
        if governance:
            body["governance"] = governance
        # Sprint 1 2026-06-15 — MITRE ATT&CK mapping for the primary finding.
        if mitre:
            body["mitre"] = mitre
        return JSONResponse(status_code=status_code, content=body)

    async def _record_runaway_failure(self, tenant_id: str, agent_id: str, tool: str,
                                       reason: str = "") -> None:
        """Tick the per-(agent, tool) failure sliding window and auto-
        quarantine on threshold breach. Wrapped so the deny path stays
        synchronous and never blocks on Redis.

        GAP-4 2026-06-15 — also tick the bulk-PII-specific counter when the
        deny reason matches a bulk-PII rule. Tighter quarantine threshold
        (3 / 5min) keeps a compromised loop from reading 50K SSN sets before
        the runaway counter fires.
        """
        try:
            from services.gateway._behavior_aggregator import (
                record_failure, quarantine_agent, is_quarantined,
                record_bulk_pii_attempt,
                RUNAWAY_FAILURE_THRESHOLD, BULK_PII_QUARANTINE_THRESHOLD,
            )
            cumulative = await record_failure(self.redis, tenant_id, agent_id, tool)
            if cumulative > RUNAWAY_FAILURE_THRESHOLD:
                already, _ = await is_quarantined(self.redis, tenant_id, agent_id)
                if not already:
                    await quarantine_agent(
                        self.redis, tenant_id, agent_id,
                        f"runaway_loop:{tool}:{cumulative}_failures_5m",
                    )
                    logger.critical(
                        "agent_auto_quarantined_runaway_loop",
                        tenant_id=tenant_id, agent_id=agent_id,
                        tool=tool, failures=cumulative,
                    )

            # GAP-4: separate bulk-PII counter — tighter ceiling.
            reason_l = (reason or "").lower()
            if ("bulk_pii" in reason_l
                    or "pii_egress" in reason_l
                    or "hc-pii" in reason_l
                    or "sec-pii" in reason_l):
                bulk_count = await record_bulk_pii_attempt(self.redis, tenant_id, agent_id)
                if bulk_count >= BULK_PII_QUARANTINE_THRESHOLD:
                    already, _ = await is_quarantined(self.redis, tenant_id, agent_id)
                    if not already:
                        await quarantine_agent(
                            self.redis, tenant_id, agent_id,
                            f"bulk_pii_loop:{bulk_count}_attempts_5m",
                        )
                        logger.critical(
                            "agent_auto_quarantined_bulk_pii_loop",
                            tenant_id=tenant_id, agent_id=agent_id,
                            attempts=bulk_count,
                        )
        except Exception as exc:
            logger.warning("runaway_loop_record_failed", error=str(exc))

    def _decision_timeout(self, request_id: str) -> JSONResponse:
        """504 Gateway Timeout for decision-service deadlines.

        Returned when the downstream Decision/Policy fan-out cannot
        complete inside the gateway SLA. `/execute` is contractually
        synchronous, so the only honest answer to "took too long" is a
        timeout status code — never 202. The transparency-chain audit row
        is emitted by the caller before this helper runs.
        """
        return JSONResponse(
            status_code=504,
            content={
                "success": False,
                "error": "decision_timeout",
                "detail": "Decision pipeline exceeded the gateway deadline.",
                "meta": {"code": 504, "category": "timeout", "request_id": request_id},
            },
        )

    def _escalate(self, message: str, *,
                  findings: list[str] | None = None,
                  reason: str | None = None,
                  policy_id: str | None = None,
                  risk_score: int | None = None,
                  explanation: str | None = None,
                  security: dict | None = None,
                  governance: dict | None = None,
                  mitre: dict | None = None) -> JSONResponse:
        """ESCALATE = needs human approval.

        2026-05-15 — /execute is contractually synchronous. The previous
        202 Accepted response implied a polling URL that never existed, so
        SDKs hit "no result body" on the happy-path side. We now return 403
        with `error: "approval_required"` so:

        * The SDK's existing DeniedError path captures it cleanly (an SDK
          subclass `EscalationRequiredError` lets callers branch when they
          care to distinguish "policy denial" from "needs approval").
        * Load-test correctness metrics still treat it as a security
          rejection, not a backend crash.
        * The out-of-band approval workflow lives unchanged at
          /autonomy/overrides — once an admin approves, retrying the
          call succeeds with 200.

        Body shape stays compatible with `_deny()` (error / meta).
        """
        logger.info("action_escalated", message=message)

        # Sprint B follow-up 2026-06-14 — escalates are also "failures" from
        # the runaway-loop perspective: an agent that gets escalated 50+
        # times in 5 minutes is misbehaving regardless of approval semantics.
        # Hook the same record_failure path as _deny so the counter ticks.
        ctx = structlog.contextvars.get_contextvars()
        t_id = ctx.get("tenant_id")
        a_id = ctx.get("agent_id")
        if t_id and t_id != "unknown" and a_id and a_id != "unknown":
            tool = ctx.get("tool") or "unknown_tool"
            _esc_reason = (reason or policy_id or "") or ""
            asyncio.create_task(_safe_bg(
                self._record_runaway_failure(str(t_id), str(a_id), str(tool), _esc_reason)
            ))

        # 2026-06-15 — surface findings + raw reason at top level so the
        # SDK's `decision.findings` is the rule name (e.g.
        # `wire_external_high_value_approval_required`) instead of the
        # opaque `approval_required` string. Buyer-visible UX gap closed.
        body: dict[str, Any] = {
            "success": False,
            "error":   "approval_required",
            "detail":  message,
            "meta":    {"code": 403, "category": "escalation"},
        }
        if findings:
            body["findings"] = list(findings)
        if reason:
            body["reason"] = reason
        # ARCH-4 2026-06-15 — explainability surface.
        if policy_id:
            body["policy_id"] = policy_id
        if risk_score is not None:
            body["risk_score"] = int(risk_score)
        if explanation:
            body["explanation"] = explanation
        # FUP-4 2026-06-15 — engine slices.
        if security:
            body["security"] = security
        if governance:
            body["governance"] = governance
        # Sprint 1 2026-06-15 — MITRE ATT&CK mapping.
        if mitre:
            body["mitre"] = mitre
        return JSONResponse(status_code=403, content=body)
