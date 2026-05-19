"""
_ResponseMixin — response builder helpers extracted from SecurityMiddleware.
All methods use ``self.redis`` which is initialised by SecurityMiddleware.__init__
at runtime.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import Response
from fastapi.responses import JSONResponse

from sdk.common.background import safe_bg as _safe_bg
from services.gateway.inference_proxy import inference_proxy

logger = structlog.get_logger(__name__)


class _ResponseMixin:
    # H-6 FIX (2026-05-13): Maximum bytes we'll buffer for output redaction.
    # Beyond this we pass the response through unredacted (streaming) and log
    # a warning. Without this cap, large LLM responses OOM the gateway worker.
    _MAX_REDACT_BUFFER_BYTES = 256 * 1024  # 256 KB

    async def _filter_response(self, response: Response) -> Response:
        content_type = response.headers.get("content-type", "")
        # Streaming or non-text content: pass through (cannot safely buffer).
        if "stream" in content_type or "event-stream" in content_type:
            return response
        if not any(t in content_type for t in ("json", "text", "xml")):
            return response

        # If Content-Length advertises a payload above the buffer cap, skip.
        try:
            advertised = int(response.headers.get("content-length", "0"))
        except ValueError:
            advertised = 0
        if advertised and advertised > self._MAX_REDACT_BUFFER_BYTES:
            logger.warning(
                "output_redaction_skipped_large_payload",
                content_length=advertised,
                cap=self._MAX_REDACT_BUFFER_BYTES,
            )
            return response

        try:
            body = bytearray()
            async for chunk in response.body_iterator:
                body.extend(chunk if isinstance(chunk, bytes) else chunk.encode())
                if len(body) > self._MAX_REDACT_BUFFER_BYTES:
                    logger.warning(
                        "output_redaction_truncated",
                        buffered=len(body),
                        cap=self._MAX_REDACT_BUFFER_BYTES,
                    )
                    # Return original (unbuffered) body up to this point; safer
                    # than holding multi-MB in memory per concurrent request.
                    return Response(
                        content=bytes(body),
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        media_type=response.media_type,
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

    def _deny(self, message: str, status_code: int) -> JSONResponse:
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

            # Metrics bounds implicitly fail-open being spawned as background Tasks without `try-except`.

            if t_id and t_id != "unknown":
                self._process_autonomous_abuse(str(t_id), str(c_ip), str(u_ag))

        return JSONResponse(
            status_code=status_code,
            content={
                "success": False,
                "error": message,
                "meta": {"code": status_code},
            },
        )

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

    def _escalate(self, message: str) -> JSONResponse:
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
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "error": "approval_required",
                "detail": message,
                "meta": {"code": 403, "category": "escalation"},
            },
        )
