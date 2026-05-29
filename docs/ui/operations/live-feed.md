# Live Feed

## What this page is for

Live Feed is the real-time event stream of the platform. It pipes Server-Sent Events from the gateway to the browser so an analyst can watch decisions arrive as they happen — useful during incident response, during deploys ("is the new policy denying anything?"), and during demos ("watch this attack get blocked").

The page also backfills the most recent 50 events on mount so an operator landing here mid-incident sees recent context, not just events that arrive after the page opens.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/live-feed`.
- **Keyboard hint**: `G L`.
- **Minimum role**: `AUDITOR`. The SSE stream is a read; no role is needed beyond authentication.
- The browser carries a per-session `sse_query_token` (stored at login under `localStorage.sse_query_token`) which the gateway accepts as a query parameter when the EventSource cannot send a header.

## What you see

- **Connection status pill** — top right. `Connected` (green), `Reconnecting` (amber), or `Disconnected` (red). Heartbeats every 15 seconds.
- **Filter bar** — left. Free-text filter (tool name or finding), decision filter (`allow` / `deny` / `escalate`), and the sidebar agent scope chip.
- **Event list** — scrollable feed. Each row: timestamp, tool, decision badge, agent, primary finding, request_id. Newest at the top.
- **Click an event** — opens a detail panel with the full audit row payload, the signal breakdown, the receipt URL.
- **Backfill banner** — shown on mount: "Showing 50 events from the last hour" until the SSE stream begins delivering new events.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Backfill recent audit logs on mount | GET | `/audit/logs?limit=50&agent_id={id}` | audit |
| Subscribe to the live decision stream | GET (SSE) | `/events/stream?token={sse_query_token}` | gateway |

The SSE stream subscribes to two Redis Pub/Sub channels server-side: `acp:sse:tenant:{tenant_id}` and (when an agent is selected) `acp:sse:agent:{agent_id}`. The gateway fans the messages out to every connected browser; reconnect is automatic.

## Auto-refresh & realtime

- **SSE stream**: continuous. Heartbeat interval 15 seconds. On heartbeat timeout (`useSSE.js` watchdog at 45 seconds), the page reconnects with a fresh token read from `localStorage.sse_query_token`.
- **No polling.** The backfill is one-shot on mount; subsequent updates are SSE only.
- **Reconnect contract**: the `useSSE` hook classifies errors as `auth_expired`, `cors`, `network`, or `heartbeat_timeout` and re-attempts the connection with appropriate backoff.

## Per-agent scoping

Yes. Selecting an agent in the sidebar triggers a re-subscribe to the per-agent SSE channel and a re-backfill via `/audit/logs?agent_id=...`. The `useEffect` at `LiveFeed.jsx:166` watches `selectedAgentId`.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Filters too tight | `No events match the selected filters.` | Loosen filters. |
| Stream connected but no traffic | Empty feed with "Connected" pill | Trigger one Playground call to seed an event. |
| Backfill returned zero events | Backfill banner replaced with "No recent events" | Wait for live events; events from your test will appear. |

## Edge cases & known gotchas

- **"Disconnected" pill stuck red after a deploy**: a gateway worker restart drops every SSE connection. The page auto-reconnects within a few seconds; if it doesn't, the `sse_query_token` may have expired — log out and back in.
- **Events not arriving despite SSE shown as Connected**: an earlier production bug had uvicorn workers sharing module-level Redis pub/sub FDs after fork, causing one worker to receive every channel's messages but only forwarding its own. The fix in `services/gateway/main.py::event_generator` creates a per-request `redis.pubsub()`. If you see this pattern again, restart the gateway container.
- **Per-agent stream shows no events**: confirm the agent has produced an `/execute` call (the SSE channel `acp:sse:agent:{agent_id}` only carries events for that specific agent). The tenant-wide channel always carries everything.
- **Heartbeat timeout after laptop suspend**: the 45-second watchdog kicks the connection; reconnect is automatic.
- **Multiple browser tabs**: each tab opens its own SSE connection. The gateway scales SSE connections per-tenant; no operational concern.
- **Per-EC2 flap**: SSE traffic uses `/events/stream` which has its own nginx `location =` block (not the regex match) so it's always proxied to the gateway directly — the SPA-vs-API gating does not apply.

## Related docs

- [Gateway service](../../services/gateway.md) — emitter of SSE events
- [Audit service](../../services/audit.md) — source of the backfill
- [Flight Recorder UI](../primary/flight-recorder.md) — the per-execution detail for any event clicked in Live Feed

## Screenshot

![Live Feed](../_screenshots/live-feed.png)
