# Live Feed

## What this page is for

Live Feed is the operator's monitor wall — a real-time, per-tenant pane of glass on every security-relevant thing happening inside the gateway. It pipes Server-Sent Events from the per-tenant `/events/stream` channel to the browser so an analyst can watch decisions arrive as they happen.

Three working modes:

- **Incident response** — when a deny burst lights up the policy chip and an `llm_proxy_escalate` toast jumps to Approval Inbox, the analyst has a one-click path from "what just happened" to "act on it".
- **Deploys and policy edits** — after rolling a new pack, watch the `policy_decision` and `would_have_blocked` chips light up to confirm the new rule is firing on real traffic.
- **Live demos** — record / export a 60-second slice of the feed showing a $25M wire blocked, an escalation queued, and an approval resolved.

The page also backfills the most recent 50 audit rows on mount so an operator landing here mid-incident sees recent context, not just events arriving after the page opens. SSE deltas and backfilled rows share a single dedup signature so the same logical decision never renders twice.

## Sidebar location and role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/live-feed`.
- **Keyboard hint**: `G L`.
- **Minimum role**: `AUDITOR`. The SSE stream is a read; no role beyond authentication is required.
- **Auth carriers** (cross-reference `services/gateway/main.py::events_stream`):
  - Browser EventSource carries the httpOnly `acp_token` cookie set by `ClerkAuthBridge` on `/auth/clerk/provision`. The hook calls `withCredentials: true` so same-origin requests include it automatically.
  - SDKs and curl send `Authorization: Bearer <jwt>` instead.
  - Query-string tokens were dropped in the sprint-1 hardening pass — they leak through nginx and ALB access logs, browser history, and Referer headers.

## Event types — the 17-key registry

Backend emits, the page renders. The complete map lives in `ui/src/pages/LiveFeed.jsx::EVENT_META`; the table below groups the keys by cluster so an operator can scan them quickly. Cross-check against the publish sites by `grep -rn "publish_event(" services/gateway/`.

### LLM proxy cluster

The `/v1/messages` and `/v1/chat/completions` proxies emit on every Claude / OpenAI call so the feed mirrors real upstream traffic, not just `/execute` tool-calls.

| Event | Where it fires | Surface on the row |
|---|---|---|
| `llm_proxy_call` | `services/gateway/routers/messages.py:580` and `routers/openai_messages.py:490` after every successful or rejected upstream call. Decision tag is `allow` for 2xx, `rejected` for 401/403/429 (with `reject_reason: upstream_401` / `_403` / `_429` / `client_aborted`), `error` for 5xx | model chip, employee email chip, `{input} in · {output} out · {latency_ms} ms · ${cost_usd}` line. Latency above 1500 ms paints amber |
| `llm_proxy_escalate` | both proxies on the 202 escalate path (`messages.py:396`, `openai_messages.py:308`) | amber `escalate` pill, `matched_pattern → approver_role` line, "Review" button that opens Approval Inbox at `/approval-inbox?id=<approval_id>` |

### Approval cluster

| Event | Where it fires | Surface on the row |
|---|---|---|
| `approval_required` | published by upstream pipelines when an `/execute` decision short-circuits into an autonomy gate | purple chip, triggers the same escalation toast as `llm_proxy_escalate` |
| `approval_resolved` | `services/gateway/routers/autonomy.py::create_override` on the 200 path | green pill, decision string (`approved` or `rejected`), resolver email, approver role, resolved timestamp |

### Deny cluster

| Event | Where it fires | Surface on the row |
|---|---|---|
| `policy_decision` | `services/gateway/_mw_response.py::_deny()` — the single chokepoint every gateway-side deny passes through. Covers pre-policy hard-denies (path traversal, SQLi, dangerous code, PII exfil), main-pipeline denies, autonomy refusals, and fail-closed paths | purple chip, `deny` pill, status_code, optional `findings[]` (first 5), reason, `policy_id`, integer `risk_score` |

The `_deny()` chokepoint matters because earlier rounds emitted `policy_decision` only from the main decision pipeline; pre-policy short-circuits and autonomy refusals fell through silently. Now every deny path — pre, main, autonomy, fail-closed — fans out to the same SSE event.

### Lifecycle cluster

| Event | Surface on the row |
|---|---|
| `tool_executed` | blue chip, agent prefix, tool name |
| `agent_created` / `agent_changed` / `agent_deleted` | green / green / red chips |
| `incident_updated` | purple chip, links into Incidents |
| `insight_generated` | blue chip, threat-intel and learning-engine signals |
| `risk_updated` | amber chip, risk score colour-coded (>0.7 red, >0.4 amber, else green) |
| `behavior_flagged` | orange chip — behavioural anomaly detector fired |
| `key_revoked` | `services/gateway/routers/users.py::revoke_api_key` on a successful DELETE — carries `key_id`, `revoker_email`, `subject_kind`, `revoked_at`. Renders through the default `alert` fallback because the registry treats key revocation as a security alert |

### Shadow cluster

| Event | Surface on the row |
|---|---|
| `would_have_blocked` | orange chip — a shadow-mode rule would have denied this call in enforce mode. Operators use this during 14-day Clerk-style shadow rollouts |

### Quota and kill-switch cluster

| Event | Surface on the row |
|---|---|
| `quota_warning` | amber chip — tenant has crossed 80% of monthly cap |
| `kill_switch` | red chip — kill-switch toggled; every subsequent `/execute` will be denied tenant-wide until cleared |

## What the operator sees on the page

### Header strip — left side

- **Title** with the `Radio` icon, "Real-time security events from the gateway SSE stream" sub-label, and a scope pill that reads either "Scope: All agents (tenant-wide)" or "Scope: <agent name>" when an agent is selected in the topbar/sidebar `AgentScopePicker`.
- **Backfill spinner** "Loading recent events…" until the initial `/audit/logs?limit=50` returns.

### Header strip — right side

The right block is laid out for monitor-wall legibility, so every control is `text-sm` or larger:

- **Throughput sparkline** — top-right. Inline SVG; recharts is deliberately not used (400 KB) and is reserved for the heavier dashboards. The gauge shows the current rate as `<rate> ev/s` in tabular numerals plus a 12-bucket × 5-second sparkline polyline (60-second window). Recomputed every 5 seconds, matching the bucket size so the right-most bar always corresponds to "this very moment".
- **Connection badge** — `text-sm` pill background with a `Wifi` / `Loader2` / `WifiOff` icon. Three states:
  - `Live` — green pill, SSE socket open.
  - `Connecting` — amber pill with a spinner, EventSource is establishing or reconnecting.
  - `Disconnected — <reason>` — red pill. The `<reason>` suffix is one of `session expired`, `cookie blocked`, `network error`, or `stream stalled`, lifted from the `useSSE` hook's classified `lastError`.
- **Reconnect** button (visible only when `state !== 'open'`) — manual override for the exponential-backoff loop.
- **Pause / Resume** — pauses local capture only. SSE keeps flowing and the page resumes appending where it left off.
- **Export** — downloads the currently-visible filtered feed as `aegis-feed-<ISO>.json`. Pure client-side blob; no backend round-trip. Useful for SOC handoffs where someone wants to email a 60-second slice around. Disabled when the visible list is empty.
- **Clear** — wipes the local event buffer and resets the dedup signature set so re-arriving events render fresh.

### Stats bar

Six tiles, one per cluster anchor type (the first six keys of `EVENT_META`): LLM Call, Approval Queued, Approval Required, Approval Resolved, Risk Update, Tool Executed. Each shows a live count of that type in the current buffer.

### Filter bar — three independent axes

Filters chain with logical AND. An empty Set on any axis means "no filter on this axis".

| Axis | Built from | Notes |
|---|---|---|
| **Type** | every key in `EVENT_META` | "All types" chip clears the type axis. Each chip shows its label colour and the live count when non-zero |
| **Employee** | the top-5 `employee_email` values seen in the current buffer | counted O(n) once per render. The "clear" link wipes the employee axis only |
| **Model** | the top-5 `model` values seen in the current buffer | same shape as the employee axis |

Every chip is a focusable `<button role="button" tabIndex={0}>` that activates on Enter or Space. The cyan focus ring is keyboard-only (`focus:outline-none focus:ring-2`).

### The event list

- Newest first, capped at 200 rows in the buffer.
- Each row carries the type label (colour-coded), the decision pill (`deny` red, `escalate` amber, `allow` green), an optional model chip, an optional employee email chip, and the type-specific lines (tokens / cost / latency for `llm_proxy_call`; `matched_pattern → approver_role` for escalates; agent prefix / tool name / reason / risk score for `/execute`-pipeline events).
- A fresh-pulse dot animates for 2 seconds when a row first appears.
- On hover, every row that carries an `agent_id` or `approval_id` reveals an **Investigate** or **Review** chevron link. `llm_proxy_escalate` rows route to `/approval-inbox?id=<approval_id>`; everything else routes to `/forensics?agent=<agent_id>`.
- The list region has `aria-live="polite"` and `aria-relevant="additions"` so screen-reader users get unobtrusive "new event" cues without the entire feed being re-announced.

### Escalation toast

When an `llm_proxy_escalate` or `approval_required` event arrives, the page fires a 10-second toast through the shared `AuthContext.addToast` channel. Message format:

> `APPROVAL: "<matched_pattern>" from <employee_email> → <approver_role>`

The toast carries a **Review** button that navigates to `/approval-inbox?id=<approval_id>`. The toast fires inside `handleMessage` (not inside `EventRow`) so it surfaces even when the user has filtered the event type out of the visible list.

### Dedup

`seenSigRef` tracks signatures keyed on either `rid:<request_id>` (when the event carries one) or a tuple `tup:<tsSec>:<type>:<cost>:<pattern>`. The seconds-resolution timestamp collapses backfill rows at `ms=001` against SSE deltas at `ms=017` for the same logical event. The set resets when the agent-scope axis changes (a row that was a duplicate under the previous scope's buffer is fair game now) and when Clear is pressed.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Backfill recent audit logs on mount | GET | `/audit/logs?limit=50&agent_id={id}` | audit |
| Subscribe to the live decision stream | GET (SSE) | `/events/stream` (optional `?agent_id={uuid}`) | gateway |
| Export the filtered feed | — | client-side `Blob` | browser |

The SSE stream subscribes to up to two Redis Pub/Sub channels server-side: `acp:events:{tenant_id}` (always) and `acp:events:{tenant_id}:{agent_id}` (only when `?agent_id=` is supplied). Each `/events/stream` handler builds a fresh `redis.pubsub()` per request to bypass a subtle uvicorn-fork bug where workers shared module-level pubsub FDs.

For the per-tenant fan-out internals — Redis channel naming, the per-client bounded queue (maxsize=100), and the publisher chokepoints — see [Gateway service](../services/gateway.md#sse-event-stream).

## Auto-refresh and realtime

- **SSE stream**: continuous. Heartbeat from the backend every 15 seconds.
- **Watchdog**: `useSSE` polls every 10 seconds; if the gap since the last heartbeat exceeds 45 seconds, the EventSource is force-closed and reconnected so the browser actually re-resolves DNS instead of sitting on a half-open TCP socket.
- **Reconnect classifier**: errors are tagged `auth_expired`, `cors`, `network`, `heartbeat_timeout`, or `unknown`. The Disconnected pill prints the human label.
- **Clerk JWT rotation fast-path**: Clerk tokens TTL at 60 seconds. When `localStorage.acp_token_expiry` shows the session was just rotated (expiry more than 30 seconds in the future) and the last failure was not `auth_expired`, `useSSE` skips the exponential backoff and reconnects in 1 second. Capped at 5 consecutive fast-reconnects to prevent livelock. Without this fast-path the Disconnected pill would blink every rotation cycle.
- **Backoff envelope**: exponential up to 32 seconds for non-rotation failures.
- **No polling**: the backfill is one-shot on mount; subsequent updates are SSE-only.

## Per-agent scoping

Selecting an agent in the `AgentScopePicker` (sidebar or topbar) triggers two re-subscribes:

1. SSE re-subscribe to the merged tenant + agent channel (`?agent_id=<uuid>`).
2. Backfill re-fetch via `/audit/logs?agent_id=…`.

The `useEffect` keyed on `selectedAgentId` lives at `ui/src/pages/LiveFeed.jsx::326` and the dedup signature set is reset on every scope change. Tenant-wide events (`kill_switch`, `quota_warning`) still arrive on per-agent scope because the gateway merges both channels into one stream.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Filters too tight | `No events match the selected filters.` | Loosen filters or click `All types`, or the per-axis `clear` link |
| Stream connected but no traffic | Empty feed with the green `Live` pill | Trigger one Playground call or one `/v1/messages` proxy call to seed an event |
| Backfill returned zero events | The backfill spinner exits silently and the list is empty | Wait — your first live event will populate |
| Connection state is `Disconnected` | List shows "Connect to start receiving events." | Click Reconnect or refresh — `useSSE` is also actively reconnecting in the background |

## Edge cases and known gotchas

- **"Disconnected" pill stuck red after a deploy**. A gateway worker restart drops every SSE connection. The page reconnects within a few seconds; if not, the cookie may have expired — log out and back in.
- **Events not arriving despite the badge saying `Live`**. An earlier production bug had uvicorn workers sharing module-level Redis pub/sub FDs after fork — publishes reached Redis but the shared subscriber's reader didn't deliver. The fix at `services/gateway/main.py::event_generator` builds a fresh `redis.pubsub()` per SSE handler so there is no cross-worker FD sharing. If the symptom reappears, restart the gateway container and check the logs for `sse_invalid_agent_id` or `sse_auth_failed`.
- **Toast fires but the row never appears**. The toast handler runs before the type-axis filter, so it surfaces even when the type is filtered out of view. This is intentional — an approval should never be hidden behind a filter chip.
- **Per-agent stream shows no events**. Confirm the agent has produced an `/execute` call. The agent channel only carries that agent's events; the tenant channel always carries everything.
- **Heartbeat timeout after laptop suspend**. The 45-second watchdog kicks the connection; reconnect is automatic.
- **Multiple browser tabs**. Each tab opens its own SSE connection. The gateway scales connections per tenant; no operational concern.
- **Throughput gauge stays at 0.0 ev/s during a quiet stretch**. Expected — the rate is computed from the right-most full bucket (latest 5 seconds). Open the Playground and fire one tool call to confirm the sparkline ticks.
- **Per-EC2 flap**. SSE traffic uses `/events/stream` which has its own nginx `location =` block (not the regex match) so it is always proxied to the gateway — the SPA-vs-API `Sec-Fetch-Mode` gating does not apply.

## Related docs

- [Gateway service](../services/gateway.md) — emitter of every SSE event, owner of the per-tenant fan-out, owner of the `/events/stream` auth contract.
- [Audit service](../services/audit.md) — source of the backfill payload.
- [Approval Inbox](../primary/incidents.md#approval-inbox) — landing surface for the **Review** button on every escalation toast and `llm_proxy_escalate` row. (The dedicated Approval Inbox page is documented under U9.)
- [Flight Recorder](../primary/flight-recorder.md) — per-execution drill-down for any `tool_executed` or `policy_decision` row clicked in Live Feed.
- [Forensics](forensics.md) — destination for the **Investigate** button on rows that carry an `agent_id`.

## Screenshot

![Live Feed](../_screenshots/live-feed.png)
