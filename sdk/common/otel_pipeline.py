"""
Sprint 3.2 — OpenTelemetry instrumentation helper for the 11-stage gateway
pipeline.

The audit (C19) found that ``forensics replay`` was timeline re-rendering,
not deterministic re-execution. Sprint 3 closes that by emitting a real
distributed trace per ``/execute`` decision: one root span for the
decision and one child span per pipeline stage, attributed with the
**OpenTelemetry GenAI semantic conventions**:

  * ``gen_ai.system``       — fixed to ``"aegis"``
  * ``gen_ai.operation.name`` — e.g. ``"execute"``, ``"stage.policy"``
  * ``gen_ai.request.model`` — when the stage knows the upstream LLM
  * ``gen_ai.usage.input_tokens`` / ``output_tokens``
  * ``gen_ai.usage.cost``    — USD (from the same price table as
    ``sdk/common/inference_cost``)
  * ``aegis.stage``         — the 11-stage canonical name
  * ``aegis.outcome``       — allow / deny / throttle / escalate / kill / skipped
  * ``aegis.risk_score``    — float in [0, 1]
  * ``aegis.findings``      — list of canonical finding codes
  * ``aegis.tenant_id`` / ``aegis.agent_id`` / ``aegis.tool``

The exporter is intentionally vendor-neutral. Sprint 8 ships
CloudWatch / Datadog / Grafana exporters that consume these spans without
any code change here.

This module avoids ``opentelemetry-instrumentation-*`` extras so it
imports cleanly in CI without the full OTel boot. When the tracer
provider is not configured (e.g., the audit service starting up before
the OTel SDK boot has finished), every helper degrades to a no-op rather
than raising.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

try:
    from opentelemetry import trace
    from opentelemetry.trace import SpanKind, Status, StatusCode
    _HAS_OTEL = True
except ImportError:  # graceful no-op when OTel isn't installed yet
    trace = None  # type: ignore[assignment]
    SpanKind = None  # type: ignore[assignment]
    Status = StatusCode = None  # type: ignore[assignment]
    _HAS_OTEL = False


# Canonical Aegis span / attribute names. Constants live here (not inline)
# so a single rename surfaces every call site at code-review time.
SPAN_DECISION_ROOT = "aegis.decision"
SPAN_STAGE_PREFIX  = "aegis.stage."

ATTR_SYSTEM        = "gen_ai.system"
ATTR_OPERATION     = "gen_ai.operation.name"
ATTR_MODEL         = "gen_ai.request.model"
ATTR_TOKENS_IN     = "gen_ai.usage.input_tokens"
ATTR_TOKENS_OUT    = "gen_ai.usage.output_tokens"
ATTR_COST_USD      = "gen_ai.usage.cost"

ATTR_STAGE         = "aegis.stage"
ATTR_OUTCOME       = "aegis.outcome"
ATTR_RISK          = "aegis.risk_score"
ATTR_FINDINGS      = "aegis.findings"
ATTR_TENANT_ID     = "aegis.tenant_id"
ATTR_AGENT_ID      = "aegis.agent_id"
ATTR_TOOL          = "aegis.tool"
ATTR_REQUEST_ID    = "aegis.request_id"
ATTR_SESSION_ID    = "aegis.session_id"

SYSTEM_VALUE       = "aegis"


def _tracer():
    """Return a tracer or None when OTel isn't configured.

    Caches the lookup at module level so repeated calls don't re-acquire
    the provider on the hot path.
    """
    global _CACHED_TRACER
    if _CACHED_TRACER is not None:
        return _CACHED_TRACER
    if not _HAS_OTEL:
        return None
    try:
        _CACHED_TRACER = trace.get_tracer("aegis.gateway.pipeline")
    except Exception:
        _CACHED_TRACER = None
    return _CACHED_TRACER


_CACHED_TRACER: Any = None


def _set_attrs(span: Any, attrs: dict[str, Any]) -> None:
    """Set a batch of attributes on ``span``, skipping ``None`` values so
    they don't pollute the trace with empty entries."""
    if span is None:
        return
    for k, v in attrs.items():
        if v is None:
            continue
        try:
            span.set_attribute(k, v)
        except Exception:
            # Attribute setters can raise on malformed values (e.g. a dict
            # passed where OTel expects a scalar). Surfacing those would
            # corrupt the request path; the trace is best-effort.
            continue


@contextmanager
def decision_span(
    *,
    request_id: str,
    tenant_id: str | None,
    agent_id: str | None,
    tool: str | None,
    session_id: str | None = None,
) -> Iterator[Any]:
    """Open the root span for one ``/execute`` decision.

    Use as a context manager so all 11 stage child-spans land inside it:

        with decision_span(request_id=rid, ...) as root:
            with stage_span("auth", request_id=rid) as s:
                ...
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(
        SPAN_DECISION_ROOT,
        kind=SpanKind.SERVER,
    ) as span:
        _set_attrs(span, {
            ATTR_SYSTEM:     SYSTEM_VALUE,
            ATTR_OPERATION:  "execute",
            ATTR_REQUEST_ID: request_id,
            ATTR_TENANT_ID:  tenant_id,
            ATTR_AGENT_ID:   agent_id,
            ATTR_TOOL:       tool,
            ATTR_SESSION_ID: session_id,
        })
        yield span


@contextmanager
def stage_span(
    stage_name: str,
    *,
    request_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Iterator[Any]:
    """Open a child span for a pipeline stage.

    ``stage_name`` is the canonical 11-stage name (``kill_switch``, ``auth``,
    ``rate_limit``, ``inference_proxy``, ``policy``, ``behavior``,
    ``decision``, ``autonomy``, ``execution``, ``output_filter``, ``audit``).
    ``extra`` lets the caller stamp the stage-specific outcome / latency
    after the work runs.
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(
        f"{SPAN_STAGE_PREFIX}{stage_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        _set_attrs(span, {
            ATTR_SYSTEM:     SYSTEM_VALUE,
            ATTR_OPERATION:  f"stage.{stage_name}",
            ATTR_STAGE:      stage_name,
            ATTR_REQUEST_ID: request_id,
        })
        if extra:
            _set_attrs(span, extra)
        yield span


def annotate_stage(
    span: Any,
    *,
    outcome: str | None = None,
    risk_score: float | None = None,
    findings: list[str] | None = None,
    latency_ms: float | None = None,
    model: str | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost_usd: float | None = None,
    error: str | None = None,
) -> None:
    """Stamp the per-stage outcome onto an open span.

    All arguments are optional so a stage that skips a check can omit
    the fields that don't apply.
    """
    if span is None:
        return
    _set_attrs(span, {
        ATTR_OUTCOME:    outcome,
        ATTR_RISK:       risk_score,
        # OTel attribute values must be scalars or lists of scalars; coerce
        # findings to a stable JSON-encoded list to keep span exporters happy.
        ATTR_FINDINGS:   findings if isinstance(findings, list) else None,
        "aegis.latency_ms": latency_ms,
        ATTR_MODEL:      model,
        ATTR_TOKENS_IN:  tokens_in,
        ATTR_TOKENS_OUT: tokens_out,
        ATTR_COST_USD:   cost_usd,
    })
    if error and _HAS_OTEL:
        try:
            span.set_status(Status(StatusCode.ERROR, error))
        except Exception:
            pass
