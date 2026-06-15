"""
Sprint 8 — OpenTelemetry exporter wiring.

Sprint 3 emitted vendor-neutral OTel spans (sdk/common/otel_pipeline.py).
This module installs the BACKEND that ships those spans to the
buyer's existing observability stack — CloudWatch GenAI observability,
Datadog, Grafana Cloud / Tempo, Honeycomb, or any OTLP-compatible
endpoint. We never replace their console; we land Aegis decisions
inside the dashboards they already pay for.

The positioning the audit/strategy doc calls out:

    "CloudWatch tells you what your agents did. Aegis decides what
    they're allowed to do — and we integrate with your observability,
    we don't replace it."

This file is that integration.

Configuration
=============
All knobs are environment variables so SSM Parameter Store is the
canonical credential path (matches Sprint 4's run_e2e.sh convention):

    AEGIS_OTEL_EXPORTER_ENABLED   "true" to install at process start (default off)
    AEGIS_OTEL_EXPORTER_PROTOCOL  "http/protobuf" (default) | "grpc"
    AEGIS_OTEL_EXPORTER_ENDPOINT  e.g. https://api.datadoghq.com/api/intake/otlp/api/v0.1/traces
    AEGIS_OTEL_EXPORTER_HEADERS   "k=v,k2=v2" — for Datadog API key, Grafana basic auth, etc.
    AEGIS_OTEL_SERVICE_NAME       resource attribute; defaults to "aegis-gateway"
    AEGIS_OTEL_BATCH_DELAY_MS     BatchSpanProcessor schedule_delay (default 5000)

Backend recipes
===============

Datadog (US1 site):
    AEGIS_OTEL_EXPORTER_ENDPOINT=https://api.datadoghq.com
    AEGIS_OTEL_EXPORTER_HEADERS=DD-API-KEY=<key>

Grafana Cloud (Tempo OTLP HTTP):
    AEGIS_OTEL_EXPORTER_ENDPOINT=https://tempo-prod-XX-prod-eu-west-2.grafana.net/otlp
    AEGIS_OTEL_EXPORTER_HEADERS=Authorization=Basic <token>

CloudWatch GenAI Observability (via the AWS OTel Collector):
    AEGIS_OTEL_EXPORTER_ENDPOINT=http://<adot-collector-endpoint>:4318
    (no headers — the collector handles SigV4 to the CloudWatch backend)

The module is a NO-OP when ``AEGIS_OTEL_EXPORTER_ENABLED`` is unset, so a
service can call ``setup_exporter()`` unconditionally in its lifespan.
"""
from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def _parse_headers(raw: str) -> dict[str, str]:
    """Parse a 'k=v,k2=v2' env value into a dict.

    Whitespace around keys/values is stripped. Empty pairs are ignored.
    Used by both the OTLP HTTP and gRPC exporters.
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        key = key.strip()
        value = value.strip()
        if key and value:
            out[key] = value
    return out


def _resource(service_name: str) -> Any:
    from opentelemetry.sdk.resources import Resource
    return Resource.create(
        {
            "service.name":      service_name,
            "service.namespace": "aegis",
            "service.version":   os.getenv("AEGIS_VERSION", "0.0.0-dev"),
            "deployment.environment": os.getenv(
                "AEGIS_ENV", "reference-deployment"
            ),
        }
    )


def setup_exporter(*, service_name: str | None = None) -> bool:
    """Install an OTLP exporter on the current process's TracerProvider.

    Returns True if the exporter was installed, False if disabled or if
    a hard requirement was missing. Never raises — callers can wire it
    unconditionally into their lifespan without try/except.
    """
    enabled = os.getenv("AEGIS_OTEL_EXPORTER_ENABLED", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False

    protocol = os.getenv(
        "AEGIS_OTEL_EXPORTER_PROTOCOL", "http/protobuf"
    ).strip().lower()
    endpoint = os.getenv("AEGIS_OTEL_EXPORTER_ENDPOINT", "").strip()
    if not endpoint:
        logger.warning(
            "otel_exporter_missing_endpoint",
            hint="Set AEGIS_OTEL_EXPORTER_ENDPOINT (e.g. Datadog OTLP intake URL).",
        )
        return False

    headers = _parse_headers(os.getenv("AEGIS_OTEL_EXPORTER_HEADERS", ""))
    name = service_name or os.getenv("AEGIS_OTEL_SERVICE_NAME", "aegis-gateway")

    try:
        from opentelemetry import trace as _trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        if protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcOTLPSpanExporter,
            )
            exporter: Any = GrpcOTLPSpanExporter(
                endpoint=endpoint, headers=headers or None,
            )
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpOTLPSpanExporter,
            )
            exporter = HttpOTLPSpanExporter(
                endpoint=endpoint, headers=headers or None,
            )

        provider = TracerProvider(resource=_resource(name))
        delay_ms = int(os.getenv("AEGIS_OTEL_BATCH_DELAY_MS", "5000"))
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                schedule_delay_millis=max(1000, delay_ms),
            )
        )
        _trace.set_tracer_provider(provider)
        logger.info(
            "otel_exporter_installed",
            backend=endpoint,
            protocol=protocol,
            service=name,
            header_keys=sorted(headers.keys()),
        )
        return True
    except Exception as exc:
        # Swallow + log — a missing OTel exporter is never a reason to
        # crash the audit / gateway process. The /metrics surface stays
        # intact; the buyer just loses traces in their backend until the
        # config is fixed.
        logger.exception("otel_exporter_install_failed", error=str(exc))
        return False


def shutdown_exporter() -> None:
    """Flush + shutdown the active TracerProvider.

    Called from each service's lifespan teardown so in-flight batches
    aren't dropped on graceful exit. No-op when no provider is installed
    or when the SDK isn't available.
    """
    try:
        from opentelemetry import trace as _trace
        provider = _trace.get_tracer_provider()
        shutdown = getattr(provider, "shutdown", None)
        if callable(shutdown):
            shutdown()
            logger.info("otel_exporter_shutdown")
    except Exception:
        logger.exception("otel_exporter_shutdown_failed")
