"""
Sprint 3.2 — OpenTelemetry pipeline-instrumentation helper tests.

The helpers in ``sdk/common/otel_pipeline`` are the integration point Sprint 8
exporters will hook into. These tests pin:

  * Root + child span hierarchy comes out right (Decision Explorer needs the
    tree to render).
  * GenAI semantic-convention attributes land on the spans with the
    correct names (so vendor exporters that read them as-is work).
  * Helpers degrade to no-ops when the global OTel provider is the default
    NoOpTracerProvider — gateway boot before OTel SDK init must NOT raise.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

# In-memory exporter so spans can be inspected by the tests without a real
# OTLP collector.
otel_sdk = pytest.importorskip("opentelemetry.sdk.trace")
from opentelemetry import trace as _otel_trace  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: E402
    InMemorySpanExporter,
)


# OpenTelemetry pins the global provider after the first ``set_tracer_provider``
# call (the SDK's ``_TRACER_PROVIDER_SET_ONCE`` guard). The session-scoped
# fixture installs one in-memory exporter for the whole test module; each
# test gets a fresh-cleared exporter via the function-scoped fixture below.
_EXPORTER = InMemorySpanExporter()
_PROVIDER = TracerProvider()
_PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
_otel_trace.set_tracer_provider(_PROVIDER)


@pytest.fixture
def span_exporter():
    """Yield the module-scoped exporter, cleared between tests."""
    _EXPORTER.clear()
    import sdk.common.otel_pipeline as op
    # Force a fresh tracer lookup so the helper picks up the provider above
    # even if a previous test cached the no-op tracer.
    op._CACHED_TRACER = None
    yield _EXPORTER


def test_decision_root_span_carries_genai_attributes(span_exporter):
    from sdk.common.otel_pipeline import decision_span

    with decision_span(
        request_id="req-123",
        tenant_id="tenant-1",
        agent_id="agent-1",
        tool="db.query",
        session_id="sess-1",
    ):
        pass

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    root = spans[0]
    assert root.name == "aegis.decision"
    attrs = dict(root.attributes)
    assert attrs["gen_ai.system"] == "aegis"
    assert attrs["gen_ai.operation.name"] == "execute"
    assert attrs["aegis.request_id"] == "req-123"
    assert attrs["aegis.tenant_id"] == "tenant-1"
    assert attrs["aegis.agent_id"] == "agent-1"
    assert attrs["aegis.tool"] == "db.query"
    assert attrs["aegis.session_id"] == "sess-1"


def test_stage_span_is_child_of_decision_span(span_exporter):
    from sdk.common.otel_pipeline import decision_span, stage_span

    with decision_span(
        request_id="req-2", tenant_id="t", agent_id="a", tool="x",
    ):
        with stage_span("auth", request_id="req-2"):
            pass
        with stage_span("policy", request_id="req-2"):
            pass

    spans = span_exporter.get_finished_spans()
    # Two child spans + one root span. Child spans close first in OTel.
    assert {s.name for s in spans} == {
        "aegis.decision", "aegis.stage.auth", "aegis.stage.policy",
    }
    root = next(s for s in spans if s.name == "aegis.decision")
    children = [s for s in spans if s.name.startswith("aegis.stage.")]
    for c in children:
        assert c.parent is not None
        assert c.parent.span_id == root.context.span_id


def test_annotate_stage_writes_outcome_and_risk(span_exporter):
    from sdk.common.otel_pipeline import (
        ATTR_FINDINGS,
        ATTR_OUTCOME,
        ATTR_RISK,
        ATTR_TOKENS_IN,
        ATTR_TOKENS_OUT,
        annotate_stage,
        decision_span,
        stage_span,
    )

    with decision_span(
        request_id="req-3", tenant_id="t", agent_id="a", tool="x",
    ):
        with stage_span("decision", request_id="req-3") as span:
            annotate_stage(
                span,
                outcome="deny",
                risk_score=0.91,
                findings=["SQL_DDL_DESTRUCTION", "PII_ACCESS_REQUESTED"],
                latency_ms=12.5,
                tokens_in=128,
                tokens_out=42,
                cost_usd=0.085,
            )

    decision = next(
        s for s in span_exporter.get_finished_spans()
        if s.name == "aegis.stage.decision"
    )
    attrs = dict(decision.attributes)
    assert attrs[ATTR_OUTCOME] == "deny"
    assert attrs[ATTR_RISK] == pytest.approx(0.91)
    assert list(attrs[ATTR_FINDINGS]) == [
        "SQL_DDL_DESTRUCTION", "PII_ACCESS_REQUESTED",
    ]
    assert attrs[ATTR_TOKENS_IN] == 128
    assert attrs[ATTR_TOKENS_OUT] == 42


def test_helpers_are_noops_when_otel_unconfigured():
    """Boot order is real: the audit service brings up middleware before the
    OTel SDK boot finishes. Calling the helpers during that window must not
    raise; the spans simply don't get emitted."""
    import sdk.common.otel_pipeline as op
    # Force the module to behave as if OTel is unavailable.
    with patch.object(op, "_HAS_OTEL", False), patch.object(op, "_CACHED_TRACER", None):
        with op.decision_span(
            request_id="x", tenant_id="t", agent_id="a", tool="y",
        ) as root:
            with op.stage_span("auth", request_id="x") as child:
                op.annotate_stage(child, outcome="allow")
            assert root is None
            assert child is None


def test_annotate_stage_silently_ignores_unsettable_values(span_exporter):
    """Some attributes (e.g. dict-valued metadata) would otherwise raise on
    set_attribute. The helper must swallow those so the request path doesn't
    crash on telemetry corruption."""
    from sdk.common.otel_pipeline import annotate_stage, decision_span, stage_span

    with decision_span(
        request_id="req-4", tenant_id="t", agent_id="a", tool="x",
    ):
        with stage_span("policy", request_id="req-4") as span:
            # ``findings`` should be a list; passing a dict triggers the
            # guard inside ``_set_attrs`` which silently skips the attribute.
            annotate_stage(span, findings={"this": "is wrong"})  # type: ignore[arg-type]
            annotate_stage(span, outcome="allow")

    policy_span = next(
        s for s in span_exporter.get_finished_spans()
        if s.name == "aegis.stage.policy"
    )
    attrs = dict(policy_span.attributes)
    assert attrs["aegis.outcome"] == "allow"
    assert "aegis.findings" not in attrs
