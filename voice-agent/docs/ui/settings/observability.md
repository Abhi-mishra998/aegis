# Observability

## What this page is for

The single-pane view of "what is the platform actually doing right now": decision throughput, recent risk signals, signal-weight configuration, and the live insight feed from the Insight worker. Reliability engineers open it to confirm SLO compliance; security engineers open it to confirm rules are firing as expected.

## Sidebar location & role gating

- **Sidebar group**: Settings → Operations.
- **Path**: `/observability`.
- **Keyboard hint**: `G O`.
- **Minimum role**: `AUDITOR`.

## What you see

- **Audit summary tiles** — Total decisions, Allow rate, Deny rate, Escalation rate.
- **Decision history feed** — last 50 decisions in chronological order; scoped to the selected agent if one is picked.
- **Risk signal breakdown panel** — the 5 risk signals (inference, policy, behavior, autonomy, agent_risk_level) with their current weights. Seeded from the first history row that includes `metadata_json.signals`.
- **Risk Scoring Formula** — reads from `/risk/signal-weights`; shows the live weights so an operator can verify any per-tenant override.
- **Insights stream** — the most recent insight cards produced by the Insight worker. Each card is a short LLM-generated explanation of a recent decision.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Audit summary | GET | `/audit/logs/summary?agent_id=...` | audit |
| Decision history | GET | `/decision/history?limit=50&agent_id=...` | decision |
| Recent insights | GET | `/insights/recent` | insight |
| Signal weights | GET | `/risk/signal-weights` | decision |

## Auto-refresh & realtime

- **Metrics interval**: 5 minutes (`300_000` ms) at `ui/src/pages/Observability.jsx:517`.
- **Insights interval**: shorter — refreshes every minute so the stream feels live.
- **No SSE.** Live Feed is the SSE-driven page; this one is poll-based for the lower-frequency aggregates.

## Per-agent scoping

Yes. Every call accepts an optional `agent_id`. Selecting an agent in the sidebar narrows the summary, the history, and the insight stream to that agent.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No summary data | The tiles read zeros | Trigger one Playground call. |
| Insights stream empty | Stream area empty | Confirm `acp_insight_worker` is consuming `acp:groq_events`. |

## Edge cases & known gotchas

- **Summary read from never-written Redis counters** — historic bug. Fixed by aggregating from the database. If the tiles show zero despite traffic, verify `services/audit/aggregator.py` is the live code path.
- **`/decision/history` 404 on a fresh tenant**: the decision stream is per-tenant and grows on first use. Expected for new tenants.
- **Signal weights show defaults but per-tenant override is set**: cache stale. The `acp:signal_weights:{tenant_id}` Redis key is read live; refresh the page.
- **Per-EC2 flap**: `/audit/*` and `/decision/*` are stable across both EC2s.

## Related docs

- [Decision service](../../services/decision.md)
- [Insight service](../../services/insight.md)
- [Audit service](../../services/audit.md)
- [Risk Engine UI](risk-engine.md)

## Screenshot

![Observability](../_screenshots/observability.png)
