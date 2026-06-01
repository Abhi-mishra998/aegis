# Policy Analytics

## What this page is for

The "are my policies working" dashboard. Shows hit rate, false-positive rate, and coverage gaps per active rule. Policy authors come here after shipping a rule to confirm it fires when it should and doesn't fire when it shouldn't.

## Sidebar location & role gating

- **Sidebar group**: Settings → Operations.
- **Path**: `/policy-analytics`.
- **Keyboard hint**: none.
- **Minimum role**: `AUDITOR`. Editing policies is a Policy Builder action; this page is read-only.

## What you see

- **KPI tiles** — Total decisions, Active policies, Unused policies, Block rate.
- **Alert callouts** — noisy policies (high false-positive rate), unused policies (never fired in window).
- **Per-policy table** — name, hit count, false-positive count, severity, last-fired timestamp.
- **Charts** — hourly activity, decision trend, deny-reason breakdown, finding breakdown, escalation-rate trend.
- **"Edit Policies" CTA** — top right, links to Policy Builder.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Audit summary | GET | `/audit/logs/summary` | audit |
| Recent logs sample | GET | `/audit/logs?limit=200` | audit |
| Tool breakdown | GET | `/audit/tool-breakdown` | audit |
| Hourly activity | GET | `/audit/hourly-activity` | audit |
| Decision trend | GET | `/audit/decision-trend` | audit |
| Deny reasons | GET | `/audit/deny-reasons` | audit |
| Finding breakdown | GET | `/audit/finding-breakdown` | audit |
| Escalation rate trend | GET | `/audit/escalation-rate-trend` | audit |

## Auto-refresh & realtime

- **30-second poll** via `setInterval(load, 30_000)` at `ui/src/pages/PolicyAnalytics.jsx:406`.
- **No SSE.**

## Per-agent scoping

Optional. The page can render tenant-wide or scoped to one agent; the sidebar agent picker drives the choice.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No decisions in window | `No decision data in window` | Extend the window. |
| No findings recorded | `No finding data` | Tenant has no rule firings yet. |
| No denies recorded | `No deny reason data` | Healthy — nothing was denied. |
| No trend data | `No trend data` | Tenant has insufficient history. |

## Edge cases & known gotchas

- **Hit rate 100% but FP rate 0%**: small-sample artefact for very new rules. Wait for more traffic.
- **GroupingError 500s on aggregator endpoints**: a fixed bug where asyncpg parameterized identical `func.date_trunc("day", ...)` call sites with distinct placeholders. If it reappears, verify `day_expr` is bound once and reused.
- **Tool Risk Leaderboard missing**: an earlier bug where `func.json_extract_path_text` on JSONB returned 500. Fixed; uses `.astext` now.
- **Per-EC2 flap**: aggregator endpoints are stable.

## Related docs

- [Audit service](../../services/audit.md)
- [Policy service](../../services/policy.md)
- [Policy Builder UI](../primary/policies.md)
- [Policy Sim UI](policy-sim.md)

## Screenshot

![Policy Analytics](../_screenshots/policy-analytics.png)
