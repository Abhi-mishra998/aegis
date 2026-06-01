# Security Dashboard

## What this page is for

The CISO-friendly summary of the tenant's security posture. Top-of-page tiles for risk and posture, an anomaly trend chart, a weekly heatmap, the recent high-risk events, and the cross-agent activity panel. The page is the "executive view" — meaningful enough to put on a wall, deep enough to drill into.

## Sidebar location & role gating

- **Sidebar group**: Settings → Access Control.
- **Path**: `/security`.
- **Keyboard hint**: none.
- **Minimum role**: `AUDITOR`.

## What you see

- **Audit summary tiles** — Total, allow, deny, escalate, risk.
- **Posture score** — single 0–100 number with trend arrow; derived from `securityService.getPosture`.
- **Top threats panel** — recent high-risk events ranked.
- **Incident summary tiles** — open / investigating / resolved counts.
- **Anomaly trends chart** — 30-day anomaly score over time.
- **Weekly heatmap** — 28-day day-of-week × hour-of-day grid.
- **Agent activity panel** — cross-agent activity counts.
- **Daily active agents chart** — how many agents fired calls each day.
- **Posture-score trend** — line chart of the posture score over time.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Audit summary | GET | `/audit/logs/summary` | audit |
| Recent logs | GET | `/audit/logs?limit=30` | audit |
| Top threats | GET | `/risk/top-threats` | audit |
| Incident summary | GET | `/incidents/summary` | api |
| Security posture | GET | `/security/posture` | api |
| Anomaly trends | GET | `/audit/trends?days=30` | audit |
| Weekly heatmap | GET | `/audit/weekly-heatmap?days=28` | audit |
| Agent activity | GET | `/audit/agent-activity?limit=20` | audit |
| Daily active agents | GET | `/audit/daily-active-agents?days=30` | audit |
| Posture trend | GET | `/audit/posture-score-trend?days=30` | audit |

## Auto-refresh & realtime

- **30-second poll** via `setInterval(load, 30_000)` at `ui/src/pages/SecurityDashboard.jsx:504`.
- **No SSE.**

## Per-agent scoping

Optional. The page can be tenant-wide or agent-scoped via the sidebar picker.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No decision data | `No decision data in window` | Trigger one `/execute`. |
| No agent activity | `No agent activity in window` | Same. |
| No agent data | `No agent data` | Same. |
| No risk data | `No risk data` | Same. |

## Edge cases & known gotchas

- **All charts blank for a fresh tenant**: 11 parallel calls to the audit aggregator, all returning empty. The page handles this gracefully but is uninteresting until traffic exists.
- **Posture score 100/100 with no data**: a quirk of the score formula on zero-sample input. The score becomes meaningful once at least ~30 decisions exist.
- **Per-EC2 flap**: every endpoint here is stable.

## Related docs

- [Audit service](../../services/audit.md)
- [API service](../../services/api.md)
- [Risk Engine UI](risk-engine.md)
- [Observability UI](observability.md)

## Screenshot

![Security Dashboard](../_screenshots/security-dashboard.png)
