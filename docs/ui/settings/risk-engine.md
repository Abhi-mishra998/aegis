# Risk Engine

## What this page is for

The deep-dive into the platform's risk-scoring math. Shows the 5 signals contributing to each decision, the live weights, top threats by risk, the agent's risk histogram, and the highest-risk events in the recent window. Engineers and analysts come here to tune signal weights and understand "why was this scored 0.87 instead of 0.6."

Labeled as "preview" in the sidebar — the page is fully functional but the weight-tuning UI is iterating.

## Sidebar location & role gating

- **Sidebar group**: Settings → Account.
- **Path**: `/risk`.
- **Keyboard hint**: none.
- **Minimum role**: `AUDITOR`. Tuning signal weights is `ADMIN` and goes through Observability or the API.

## What you see

- **Risk summary tiles** — Mean risk, p95 risk, % allow, % deny.
- **Risk timeline** — line chart of per-call risk score over the window.
- **Top threats list** — recent high-risk audit rows.
- **Insight stream** — recent insight cards relevant to this agent.
- **Top findings** — frequency-ranked finding list.
- **Risk histogram** — distribution of risk scores across the window.
- **High-risk events list** — full audit-row table for risk ≥ 0.7.
- **Tool risk leaderboard** — tools ranked by mean risk.
- **Signal weights** — current per-signal weights from `/risk/signal-weights`.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Risk summary | GET | `/risk/summary?agent_id=...` | audit (proxied) |
| Risk timeline | GET | `/risk/timeline` | audit |
| Top threats | GET | `/risk/top-threats` | audit |
| Recent insights | GET | `/insights/recent` | insight |
| Top findings | GET | `/audit/top-findings?days=30&limit=15&agent_id=...` | audit |
| Risk histogram | GET | `/audit/risk-histogram?days=30&agent_id=...` | audit |
| High-risk events | GET | `/audit/high-risk-events?days=7&limit=20&min_risk=0.7&agent_id=...` | audit |
| Tool risk | GET | `/audit/tool-risk?days=30&limit=20&agent_id=...` | audit |
| Signal weights | GET | `/risk/signal-weights` | decision |

## Auto-refresh & realtime

- **30-second poll** via `setInterval(load, 30_000)` at `ui/src/pages/RiskEngine.jsx:328`.
- **No SSE.**

## Per-agent scoping

Yes. Selecting an agent narrows every chart and table to that agent. Without a selection, the page renders tenant-wide.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No scored events | `No scored events in window` | Extend the window or trigger one Playground call. |
| No tool data | `No tool data in window` | Same. |
| No findings | `No findings recorded in window.` | Healthy — no findings tripped. |
| No distribution data | `No distribution data` | Insufficient sample size. |
| No trend data | `No trend data available.` | Same. |

## Edge cases & known gotchas

- **Signal weights row missing**: an earlier bug where `/risk/signal-weights` was unproxied and returned 404. Confirm the gateway proxy at `services/gateway/main.py` includes it.
- **Mean risk near 1.0 across the board**: usually a single attack-sim run flooded the histogram. Verify with the audit row IDs in the high-risk events list.
- **Per-EC2 flap**: `/risk/*` proxies are stable after the deploy-topology fix.

## Related docs

- [Decision service](../../services/decision.md)
- [Behavior service](../../services/behavior.md)
- [Observability UI](observability.md)

## Screenshot

![Risk Engine](../_screenshots/risk-engine.png)
