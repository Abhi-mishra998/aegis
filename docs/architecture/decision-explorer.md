# Decision Explorer

*Every `/execute` decision becomes a span graph — eleven pipeline stages
as nodes, stage signals as edges, the signed audit row attached to the
root. The data source is the Flight Recorder; the wire-format is
OpenTelemetry GenAI semantic conventions so any standard collector
(CloudWatch, Datadog, Grafana Tempo, Jaeger) can consume the same trace
without an Aegis-specific shim.*

## Why this page exists

The audit (C19) found that "forensics replay" was timeline re-rendering,
not deterministic re-execution — useful but not what an SRE looking at a
denied request actually needs. Sprint 3 reframes the replay surface around
a real distributed trace: one root span per decision plus one child span
per pipeline stage, attributed with token counts, USD cost, risk score,
and the canonical finding vocabulary.

The same data drives three UI views (Graph / Timeline / JSON) and the
Trace Overview KPI strip. The architecture is the focus of this page —
the operator runbook lives at
[`docs/ui/operations/decision-explorer.md`](../ui/operations/decision-explorer.md)
when that ships in the next polish pass.

## The data model

Three sources combine into one Decision Explorer payload:

| Source | Where | What it contributes |
|---|---|---|
| `execution_timelines` | `services/flight_recorder/models.py` | Request envelope: `request_id`, `session_id` (Sprint 3.5), tenant, agent, tool, started/completed timestamps, final decision + risk |
| `execution_steps` | same module | Per-stage records: `step_type`, `status`, `latency_ms`, `risk_score`, free-form `payload` |
| `execution_snapshots` | same module | Token counts and intermediate state — fed into the cost rollup |

The Flight Recorder writer at the gateway emits one Redis Streams event
per step (`emit_step` in `services/gateway/trust_emitter.py`); the
`flight_recorder` worker consumes them and writes the rows.

Pre-Sprint-3 the writer used a flat `step_type` vocabulary (`prompt`,
`tool_call`, `policy`, `decision`, `retry`, `failure`). Sprint 3
introduces a richer `payload.stage` field that the Decision Explorer
queries against (canonical 11-stage names — `kill_switch`, `auth`,
`rate_limit`, `inference_proxy`, `policy`, `behavior`, `decision`,
`autonomy`, `execution`, `output_filter`, `audit`). Old rows fall back to
a deterministic legacy-mapping in
`services/flight_recorder/router.py::_step_type_to_stage` so historical
exports continue to render.

## Backend: `/flight/decision/{request_id}/graph`

Source: `services/flight_recorder/router.py::get_decision_graph`.

The endpoint is tenant-scoped via the JWT-derived `tenant_id` dependency
(`sdk/common/db.get_tenant_id`). Cross-tenant queries are impossible —
not optional. Response shape:

```json
{
  "timeline": { "request_id": "...", "session_id": "...", "final_risk": 0.91, "duration_ms": 42, ... },
  "nodes": [
    { "id": "stage:auth",     "stage": "auth",     "outcome": "allow", "risk_score": 0.0, "latency_ms": 3,  "summary": "..." },
    { "id": "stage:policy",   "stage": "policy",   "outcome": "deny",  "risk_score": 0.95, "latency_ms": 8,  "summary": "..." }
  ],
  "edges": [
    { "source": "stage:auth", "target": "stage:policy", "signal": "allow", "risk_contribution": 0.0 }
  ],
  "receipt_url": "/receipts/<request_id>",
  "total_latency_ms": 42,
  "tokens_in": 200,
  "tokens_out": 80,
  "estimated_usd": 0.140
}
```

Edge cases pinned by `tests/test_explorer_endpoints.py`:

* The same stage written twice (e.g. a re-evaluated policy after a cache
  miss) collapses to the **highest-risk** version so the graph never
  shows duplicate nodes.
* Steps with explicit `payload.stage` win over the legacy `step_type`
  mapping; unknown explicit stages are rejected and fall back to the
  legacy lane (no silent corruption of the canonical 11-stage vocabulary).
* Missing `request_id` returns 404 (not 500 or an empty payload).

The token + USD totals come from `execution_snapshots` and use the same
price table as `sdk/common/inference_cost` so the cost number shown here
matches the per-tenant inference cost dashboard exactly.

## Backend: `/flight/sessions` and `/flight/sessions/{session_id}`

Session = the set of `execution_timelines` that share a non-NULL
`session_id`. The gateway accepts `X-Session-ID` on `/execute`
(informational header, no auth load) and propagates it through
`emit_timeline_start` to the worker, which writes it to the new
`execution_timelines.session_id` column (alembic migration
`g3c4d5e6f7a8_session_id`).

The list endpoint returns a `SessionSummary` per session for the last N
minutes (default 24 h), ordered by recency. Each summary carries:

* `decision_count`, `distinct_agents`, `distinct_tools`
* `max_risk` and `final_risk`
* A **risk trajectory** — the ordered list of `final_risk` values, used
  by the UI to draw a sparkline without a second round-trip.

The drill-down endpoint returns every timeline in chronological order
plus the full risk trajectory. Rising risk across consecutive turns is
the governance signal Omni does not surface — it's what the Session
Explorer makes visible.

## OpenTelemetry GenAI conventions

Source: `sdk/common/otel_pipeline.py`.

The 11 pipeline stages emit OTel spans following the OpenTelemetry
GenAI semantic conventions so a Sprint-8 exporter can ship them to
CloudWatch GenAI observability, Datadog LLM Observability, or any
OTLP-compatible backend without any Aegis-specific schema:

| Attribute | Set by |
|---|---|
| `gen_ai.system` | always `"aegis"` |
| `gen_ai.operation.name` | `execute` (root span); `stage.<name>` for child spans |
| `gen_ai.request.model` | the LLM model when the stage knows it |
| `gen_ai.usage.input_tokens` / `output_tokens` | filled by the inference-proxy / audit stages |
| `gen_ai.usage.cost` | computed via `sdk/common/inference_cost` price table |
| `aegis.stage` | the canonical 11-stage name |
| `aegis.outcome` | `allow` / `deny` / `throttle` / `escalate` / `kill` / `skipped` |
| `aegis.risk_score` | float in [0, 1] |
| `aegis.findings` | list of canonical finding codes |
| `aegis.tenant_id` / `aegis.agent_id` / `aegis.tool` | request context |

The gateway's `SecurityMiddleware.dispatch` opens the root
`aegis.decision` span at request entry (`services/gateway/middleware.py`).
Child stage spans are emitted via `stage_span(stage_name)` and annotated
on close with `annotate_stage(...)`. The helpers degrade to no-ops if the
OTel SDK isn't configured (e.g., during service boot before the SDK
initialiser has run), so wiring them into a stage never raises on the
hot path.

The helper module ships with `tests/test_otel_pipeline.py` (5 cases)
pinning:

* The root span carries the GenAI attribute set.
* Child stage spans are real children of the decision span (parent
  span-id matches the root's span-id).
* `annotate_stage` writes outcome / risk / findings / token counts onto
  open spans.
* The helpers are safe to call when OTel is unconfigured — no exception
  reaches the caller.

## UI

`ui/src/pages/DecisionExplorer.jsx` consumes the graph endpoint via
`flightService.getDecisionGraph(request_id)`. It offers three views:

1. **Graph** — React Flow (`reactflow` 11.11) renders stages left-to-right
   with one node per present stage and edges carrying the upstream
   signal. Node colour follows the canonical outcome palette so the
   decision verdict is legible at a glance.
2. **Timeline** — vertical list: index, stage, outcome, latency, risk,
   summary. Useful for screenshotting into a ticket.
3. **JSON** — the raw API payload. Bypassed for the operator who needs
   to paste the response into a Slack thread.

Above the views, a **Trace Overview** strip surfaces the 7 KPIs an SRE
checks first: decision, final risk, total latency, stage count, input
tokens, output tokens, estimated USD.

`ui/src/pages/SessionExplorer.jsx` has a two-pane layout: a list of
sessions on the left with per-session risk-trajectory sparklines, and a
drill-down on the right that shows the decision sequence with a larger
risk chart on top. Each decision row links straight into the Decision
Explorer.

## What's not yet wired (follow-ons)

* **Live-tail via SSE.** The Sprint 1 SSE feed already publishes
  decisions per tenant; the next polish pass will have both Explorer
  pages subscribe so a new `/execute` shows up without a refresh.
* **Stage-by-stage OTel instrumentation.** Sprint 3.2 ships the root
  span and the helper API; wiring `stage_span(...)` into each of the
  11 pipeline stage call sites is a follow-on commit. Until then, the
  trace tree shows one root with downstream HTTP children from the
  auto-instrumented FastAPI / httpx integration.
* **The OTel exporter.** Sprint 8 lands the CloudWatch / Datadog /
  Grafana exporters that consume these spans. No code change needed
  here — exporters subscribe to the OTLP endpoint the SDK already
  surfaces.

## File map

| Concern | File |
|---|---|
| Migration | `services/flight_recorder/alembic/versions/g3c4d5e6f7a8_session_id.py` |
| Model | `services/flight_recorder/models.py::ExecutionTimeline.session_id` |
| Schemas | `services/flight_recorder/schemas.py` (DecisionGraphNode/Edge/Out, SessionSummary, SessionDetailOut) |
| Endpoints | `services/flight_recorder/router.py` (get_decision_graph, list_sessions, get_session) |
| Gateway emit | `services/gateway/trust_emitter.py::emit_timeline_start` + `services/gateway/middleware.py::_dispatch_with_resilience` |
| Worker | `services/flight_recorder/worker.py::_get_or_create_timeline` + `_apply_event` |
| OTel helper | `sdk/common/otel_pipeline.py` |
| UI — Decision | `ui/src/pages/DecisionExplorer.jsx` |
| UI — Session | `ui/src/pages/SessionExplorer.jsx` |
| API client | `ui/src/services/api.js::flightService.getDecisionGraph` / `listSessions` / `getSession` |
| Sidebar nav | `ui/src/components/Layout/Sidebar.jsx` |
| Tests | `tests/test_explorer_endpoints.py` (10), `tests/test_otel_pipeline.py` (5) |

## Next

- [Cryptographic Audit Chain](../security/crypto-audit-chain.md) — what
  the `receipt_url` on the root node opens
- [Detection Pipeline](../security/detection-pipeline.md) — the upstream
  source of every stage signal the graph renders
