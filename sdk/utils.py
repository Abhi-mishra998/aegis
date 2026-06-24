from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.semconv.resource import ResourceAttributes
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

from sdk.common.config import settings
from sdk.common.exceptions import setup_exception_handlers

# Custom Metrics for ACP Hardening & Hyperscale
CIRCUIT_BREAKER_STATE_TOTAL = Counter(
    "acp_sdk_circuit_breaker_state_total",
    "Total count of circuit breaker state changes",
    ["service_name", "state"],
)

IDEMPOTENCY_HITS_TOTAL = Counter(
    "acp_idempotency_hits_total",
    "Total count of idempotency key hits",
    ["tenant_id", "outcome"],  # outcome: hit, conflict
)

IDEMPOTENCY_EVICTIONS_TOTAL = Counter(
    "acp_idempotency_evictions_total",
    "Total count of idempotency cache evictions due to memory pressure",
)

RATE_LIMIT_EXCEEDED_TOTAL = Counter(
    "acp_rate_limit_exceeded_total",
    "Total count of rate limit rejections",
    # layer: global, ip, tenant, agent, token; tier: enterprise, premium, basic
    ["layer", "tier"],
)

AUDIT_DUPLICATES_DROPPED_TOTAL = Counter(
    "acp_audit_duplicates_dropped_total",
    "Total count of logical audit duplicates dropped at ingestion",
)

BILLING_EVENTS_TOTAL = Counter(
    "acp_billing_events_total",
    "Total count of billing events attempted",
)

BILLING_EVENTS_FAILED = Counter(
    "acp_billing_events_failed",
    "Total count of billing events failed",
)

BILLING_ZERO_TOKEN_CORRECTED = Counter(
    "acp_billing_zero_token_corrected",
    "Total count of billing events where tokens were automatically corrected from <=0 to 1",
)

# SLO Metrics
SLO_AVAILABILITY_TOTAL = Counter(
    "acp_slo_availability_total",
    "Total requests for availability SLO tracking",
    ["service", "status"],  # status: success, error
)

SLO_LATENCY_SECONDS = Histogram(
    "acp_slo_latency_seconds",
    "Request latency for p99 SLO tracking",
    ["service", "route"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
    ),
)

SLO_AUDIT_DURABILITY_TOTAL = Counter(
    "acp_slo_audit_durability_total",
    "Audit record lifecycle tracking for durability SLO",
    ["stage"],  # stage: produced, ingested, persisted, dlq, dropped_at_maxlen
)

# H-10 FIX (2026-05-13): Observability for the Redis audit stream so silent
# event loss (XADD MAXLEN approximate trimming) becomes a visible signal.
AUDIT_STREAM_LENGTH = Gauge(
    "acp_audit_stream_length",
    "Approximate length of the acp:audit_stream queue",
)
AUDIT_STREAM_DROPPED_TOTAL = Counter(
    "acp_audit_stream_dropped_total",
    "Audit events dropped from the head of the stream by MAXLEN trimming",
)

# Phase 1 (2026-06-24): Producer-side validation. The audit consumer used to
# absorb every malformed event into acp:audit_stream:dlq, hiding the bad caller
# behind a generic "consumer DLQ depth" alert. Producer-side rejection moves
# the failure to a separate stream (acp:audit_stream:producer_dlq) AND a
# counter labelled by failure reason so the offending call site is debuggable
# from Prometheus alone.
AUDIT_PRODUCER_DLQ_TOTAL = Counter(
    "acp_audit_producer_dlq_total",
    "Audit events rejected at the producer (never reached the stream)",
    # reason: missing_field, invalid_tenant_uuid, invalid_request_id,
    #         producer_dlq_write_failed
    ["reason"],
)
GROQ_EVENT_FAILURES_TOTAL = Counter(
    "acp_groq_event_failures_total",
    "Count of failures emitting Groq decision events (was previously silent)",
)

# 2026-05-13: Operators need to see degraded-mode runs explicitly. The C-4 fix
# defaults Behavior risk to 0.5 on timeout/outage, but the previous version
# only surfaced this in the `reasons` list of each decision — no metric, no
# alertable signal. This counter increments once per request that ran in
# fail-CLOSED mode.
BEHAVIOR_FAIL_CLOSED_TOTAL = Counter(
    "acp_behavior_fail_closed_total",
    "Decisions evaluated with Behavior service unavailable (fail-CLOSED fallback active)",
)

# Per-consult outcome counter for the behavior firewall. Labelled by `result`:
#   ok       — behavior service returned 2xx within budget
#   timeout  — fan-out budget exceeded OR per-call read timeout
#   error    — connection refused / 5xx / parse error / any non-timeout exception
#   skipped  — request bypassed the consult (kill switch, feature flag, etc.)
BEHAVIOR_FIREWALL_CONSULT_TOTAL = Counter(
    "acp_behavior_firewall_consult_total",
    "Total behavior firewall consults by outcome",
    ["result"],
)

BEHAVIOR_FIREWALL_LATENCY_SECONDS = Histogram(
    "acp_behavior_firewall_latency_seconds",
    "Wall-clock latency of behavior firewall consults (seconds)",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.5, 5.0),
)

# Pre-warm all four labels so `/metrics` surfaces them as `0` before any
# request hits the corresponding code path. Operators expect a complete
# split, not a missing series.
for _bfw_label in ("ok", "timeout", "error", "skipped"):
    BEHAVIOR_FIREWALL_CONSULT_TOTAL.labels(result=_bfw_label).inc(0)

# Flight Recorder timeline lifecycle counters. The delta
#   acp_flight_timeline_open_total - acp_flight_timeline_closed_total
# is the canonical "leaked timelines" SLI — a non-zero gap under steady state
# means the gateway is opening timelines it never finalises. Producer-side
# counters wired in `services/gateway/trust_emitter.py`. The companion
# breakdown by terminal disposition lives on the close-counter labels.
FLIGHT_TIMELINE_OPEN_TOTAL = Counter(
    "acp_flight_timeline_open_total",
    "Total Flight Recorder timelines opened (one per /execute that reaches tool resolution)",
)
FLIGHT_TIMELINE_CLOSED_TOTAL = Counter(
    "acp_flight_timeline_closed_total",
    "Total Flight Recorder timelines closed by the gateway",
)
FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL = Counter(
    "acp_flight_timeline_closed_by_status_total",
    "Flight timelines closed broken down by terminal status (ok|failed)",
    ["status"],
)
for _ft_label in ("ok", "failed"):
    FLIGHT_TIMELINE_CLOSED_BY_STATUS_TOTAL.labels(status=_ft_label).inc(0)

# Billing reconciliation SLI. The CLI (scripts/ops/reconcile.py) computes
# a symmetric set diff between billable audit_logs and usage_records and
# POSTs the result to the gateway's /internal/reconciliation-report
# endpoint, which writes these gauges. The Alertmanager rules in
# infra/prometheus-rules.yml page when either non-zero count gauge sticks
# above 0 for >5m or when the outbox-age gauge goes above 300s.
RECONCILE_AUDIT_WITHOUT_USAGE = Gauge(
    "acp_reconcile_audit_without_usage",
    "Billable audit_logs rows with no matching usage_records row (per tenant)",
    ["tenant"],
)
RECONCILE_USAGE_WITHOUT_AUDIT = Gauge(
    "acp_reconcile_usage_without_audit",
    "usage_records rows whose audit_id does not exist in audit_logs (per tenant)",
    ["tenant"],
)
RECONCILE_OUTBOX_OLDEST_AGE_SECONDS = Gauge(
    "acp_reconcile_outbox_oldest_age_seconds",
    "Age in seconds of the oldest pending_usage_events row (per tenant). 0 when empty.",
    ["tenant"],
)

# Sprint 3.2 — tenant quota signals. RATE_LIMITED counter is per-tenant
# + limit_type so dashboards can split "noisy neighbour" (rps) vs
# "consumed-the-month" (monthly) — two very different operational
# stories. WARNING fires once per tenant per month at 80% of the
# monthly cap; the spike in this counter is what pages the on-call.
TENANT_RATE_LIMITED_TOTAL = Counter(
    "acp_tenant_rate_limited_total",
    "Requests rejected by the per-tenant quota, by limit_type.",
    ["tenant", "limit_type"],
)
TENANT_QUOTA_WARNING_TOTAL = Counter(
    "acp_tenant_quota_warning_total",
    "Monthly-cap 80% warning fires (idempotent per tenant per month).",
    ["tenant"],
)

# Sprint 3.5 — queue-age SLIs. A constant 2-row pending could be either
# healthy churn or a single stuck row from an hour ago; the AGE of the
# oldest row is what tells the operator which it is. All gauges seeded
# at 0 so /metrics surfaces them before the first refresh tick.
OUTBOX_OLDEST_PENDING_AGE_SECONDS = Gauge(
    "acp_outbox_oldest_pending_age_seconds",
    "Age in seconds of the oldest pending outbox row, per logical outbox.",
    ["outbox_name"],
)
AUDIT_DLQ_OLDEST_AGE_SECONDS = Gauge(
    "acp_audit_dlq_oldest_age_seconds",
    "Age in seconds of the oldest event in the audit DLQ stream.",
)
BILLING_DLQ_OLDEST_AGE_SECONDS = Gauge(
    "acp_billing_dlq_oldest_age_seconds",
    "Age in seconds of the oldest event in the billing DLQ list.",
)
INSIGHT_QUEUE_DEPTH = Gauge(
    "acp_insight_queue_depth",
    "Approximate length of the insight (Groq) processing queue.",
)
INSIGHT_QUEUE_OLDEST_AGE_SECONDS = Gauge(
    "acp_insight_queue_oldest_age_seconds",
    "Age in seconds of the oldest event in the insight queue.",
)
GROQ_QUEUE_DEPTH = Gauge(
    "acp_groq_queue_depth",
    "Alias for the insight queue, exposed under the producer name.",
)
GROQ_QUEUE_OLDEST_AGE_SECONDS = Gauge(
    "acp_groq_queue_oldest_age_seconds",
    "Oldest groq-queue entry age. Same source as the insight gauge.",
)
FLIGHT_TIMELINE_IN_PROGRESS_COUNT = Gauge(
    "acp_flight_timeline_in_progress_count",
    "Flight timelines older than 60s with status=in_progress (leaks).",
)
for _outbox in ("audit_to_usage",):
    OUTBOX_OLDEST_PENDING_AGE_SECONDS.labels(outbox_name=_outbox).set(0)
AUDIT_DLQ_OLDEST_AGE_SECONDS.set(0)
BILLING_DLQ_OLDEST_AGE_SECONDS.set(0)
INSIGHT_QUEUE_DEPTH.set(0)
INSIGHT_QUEUE_OLDEST_AGE_SECONDS.set(0)
GROQ_QUEUE_DEPTH.set(0)
GROQ_QUEUE_OLDEST_AGE_SECONDS.set(0)
FLIGHT_TIMELINE_IN_PROGRESS_COUNT.set(0)

# Sprint 3.5 — inference cost ceilings. Per-tenant + per-agent daily USD
# caps. Block at 100%, warn at 80%. The warning is idempotent per
# (scope, key, date) via Redis SETNX flag held by the limiter.
INFERENCE_COST_USD_TOTAL = Counter(
    "acp_inference_cost_usd_total",
    "Cumulative inference-call cost in USD (post-emission, observability only).",
    ["scope", "key"],   # scope ∈ {tenant, agent}; key = tenant_id or agent_id
)
INFERENCE_COST_BLOCKED_TOTAL = Counter(
    "acp_inference_cost_blocked_total",
    "Inference calls blocked because the daily USD cap was exceeded.",
    ["scope", "key"],
)
INFERENCE_COST_WARNING_TOTAL = Counter(
    "acp_inference_cost_warning_total",
    "80%-of-cap warnings fired (idempotent per scope/key per day).",
    ["scope", "key"],
)

# Sprint 3.5 — emitted by `services/audit/integrity.verify_audit_chain`
# every time `/audit/logs/verify` runs and finds a tampered or broken
# event_hash chain. The alertmanager rule `ChainViolationImmediate`
# (for: 0m, severity=page) pages the on-call the moment this counter
# moves. Pre-warmed to 0 so the rule is well-defined on first scrape.
AUDIT_CHAIN_VIOLATIONS_TOTAL = Counter(
    "acp_audit_chain_violations_total",
    "Cumulative audit-chain integrity violations detected by /audit/logs/verify.",
)
AUDIT_CHAIN_VIOLATIONS_TOTAL.inc(0)


# Run-3 (2026-05-14): Transactional outbox pattern — durability backstop for the
# sync billing path. The audit writer inserts pending_usage_events atomically
# with audit_logs; the audit-side outbox worker drains pending rows older than
# the sync-path SLO and forwards them to the usage service (idempotent via
# usage_records.audit_id UNIQUE constraint). These metrics make the backstop
# observable so it can be alerted on.
OUTBOX_PENDING_GAUGE = Gauge(
    "acp_outbox_pending_count",
    "Pending audit→billing outbox events awaiting drain",
)
OUTBOX_PROCESSED_TOTAL = Counter(
    "acp_outbox_processed_total",
    "Outbox events successfully forwarded to the usage service",
)
OUTBOX_RETRY_TOTAL = Counter(
    "acp_outbox_retry_total",
    "Outbox event retry attempts (transient failures)",
)
OUTBOX_POISON_TOTAL = Counter(
    "acp_outbox_poison_total",
    "Outbox events marked failed after retry_count exceeded",
)
BILLING_OUTBOX_COVERAGE_GAP_TOTAL = Counter(
    "acp_billing_outbox_coverage_gap_total",
    "Billable execute_tool events where the outbox row was silently dropped (duplicate audit_id on_conflict_do_nothing)",
)

# ── MTTR metrics (incident resolution timing) ─────────────────────────────────
# The per-severity histogram acp_incident_resolution_seconds is defined in
# services/api/router/incident.py (where it is observed on resolution).
# This gauge exposes the rolling 7-day average in seconds so Prometheus
# alerting rules can threshold directly on it without PromQL functions.
INCIDENT_MTTR_SECONDS = Gauge(
    "acp_incident_mttr_seconds",
    "Rolling 7-day mean time to resolution for incidents (seconds).",
)
INCIDENT_MTTR_SECONDS.set(0)

# ── Cost metrics ──────────────────────────────────────────────────────────────
# Gauge: per-tenant current-day cost in USD.  Set (not incremented) each time
# the usage router records a billable event so Prometheus always sees the
# running total for the day.  Label: tenant_id (string).
TENANT_DAILY_COST_USD = Gauge(
    "acp_tenant_daily_cost_usd",
    "Current calendar-day cost in USD for a tenant (1M tokens = $0.05).",
    ["tenant_id"],
)

# Counter: monotonically increasing total cost across all tenants.
TOTAL_COST_USD_TOTAL = Counter(
    "acp_total_cost_usd_total",
    "Running total cost in USD across all tenants since service start.",
)


_LOGGING_INITIALIZED = False

def setup_logging(service_name: str) -> None:
    """Configures structured JSON logging using structlog."""
    global _LOGGING_INITIALIZED

    # P3-1 FIX: Prevent double-registration of structlog in fastAPI uvicorn hot-reload loops
    if _LOGGING_INITIALIZED:
        return
    _LOGGING_INITIALIZED = True


    def add_trace_id(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
        span = trace.get_current_span()
        if span and span.get_span_context().is_valid:
            event_dict["trace_id"] = format(span.get_span_context().trace_id, "032x")
            event_dict["span_id"] = format(span.get_span_context().span_id, "016x")
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            add_trace_id,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    structlog.get_logger().info("logging_initialized", service=service_name)


def setup_tracing(app: FastAPI, service_name: str) -> None:
    """Initializes OpenTelemetry tracing across all services with OTLP Export."""
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    otlp_endpoint = settings.OTLP_ENDPOINT
    if not otlp_endpoint:
        # P2-2 FIX: Skip tracing when no OTLP collector is configured (safe for dev/Docker)
        return

    resource = Resource(
        attributes={
            ResourceAttributes.SERVICE_NAME: service_name,
            "environment": settings.ENVIRONMENT,
        }
    )

    provider = TracerProvider(resource=resource)

    # P2-2 FIX: Use configurable endpoint instead of hardcoded localhost:4317
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    # Instrument the FastAPI app automatically
    FastAPIInstrumentor.instrument_app(app)



def setup_app(app: FastAPI, service_name: str) -> None:
    """
    Consolidated setup for all ACP services.
    Includes: logging, tracing, metrics, exception handlers, and CORS.
    """
    # 1. Observability
    setup_logging(service_name)
    setup_tracing(app, service_name)
    # 2026-06-15 — monkey-patch prometheus_fastapi_instrumentator's route
    # walker. The library's _get_route_name iterates `app.routes` and reads
    # `route.path`, but FastAPI 0.110+ exposes nested includes as
    # `_IncludedRouter` objects without `.path`. The library then raises
    # AttributeError on every request through such routes, returning 500
    # for /agents POST and /agents/{id}/permissions specifically. Wrap the
    # function to skip non-path entries instead of crashing.
    try:
        from prometheus_fastapi_instrumentator import routing as _pfi_routing
        _orig_get_route_name = _pfi_routing._get_route_name
        def _safe_get_route_name(scope, routes):  # type: ignore[no-untyped-def]
            try:
                return _orig_get_route_name(scope, routes)
            except AttributeError:
                # _IncludedRouter (no .path) — fall back to scope path.
                return scope.get("path", "/")
        _pfi_routing._get_route_name = _safe_get_route_name
    except Exception:
        # Library not installed or shape changed — don't block boot.
        pass
    Instrumentator().instrument(app).expose(app)

    # 2. Security (CORS)
    # Origins come from ALLOWED_ORIGINS env var (comma-separated).
    # Default covers local dev; set to your domain in production.
    allowed_origins = [o.strip() for o in settings.ALLOWED_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID",
                       "X-Tenant-ID", "X-Agent-ID", "X-ACP-Tool", "X-Timestamp",
                       "X-Internal-Secret", "X-API-Key"],
        expose_headers=["X-Trace-ID", "X-Request-ID", "X-RateLimit-Remaining"],
    )

    # 3. Standard Exception Handlers (traps unhandled and SDK exceptions)
    setup_exception_handlers(app)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {
            "status": "healthy",
            "service": service_name,
            "version": "1.0.0",  # Pull from version.py in prod
        }
