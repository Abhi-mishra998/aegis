# Flight Recorder

## What this page is for

The Flight Recorder is the first page an operator opens when they need to answer "what exactly happened on that one request." Each row is one `/execute` decision captured end to end — every middleware stage that ran, what each one decided, how long each one took, and the inputs and outputs at each step. Operators come here during incident triage, during customer escalations ("why was my agent denied at 03:14"), and during platform debugging.

## Sidebar location & role gating

- **Sidebar group**: Primary nav (first item).
- **Path**: `/flight-recorder`.
- **Keyboard hint**: `G F`.
- **Minimum role**: `AUDITOR` (read-only). `VIEWER` can also load the page but most filter actions silently fail; write actions on the page (replay, fetch receipt) work for any authenticated role because they're pure GETs.

## What you see

Top-to-bottom on the page:

- **Filter bar** — left side. Free-text tool filter, time-window selector (5 min / 15 min / 1 hour / 6 hours), agent scope chip (driven by the sidebar `useAgents` picker).
- **Timeline list** — left column, scrollable. Each row shows the tool name, the final status (allow / deny / escalate), the duration, the timestamp, and the agent.
- **Replay panel** — right side. When you click a row, this panel populates with: the per-stage step list (one row per middleware stage with its decision, latency, and findings), the per-step snapshots (pre-decision and post-decision `request.state` dumps), the signed receipt URL, and the Merkle inclusion proof for the day's transparency root.
- **"Scope" chip** — at the top right of the replay panel, shows the active agent filter; clicking it clears the filter and returns to the all-agents view.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List recent timelines (with optional `agent_id` filter) | GET | `/flight/timelines` | flight_recorder |
| Get one timeline's full replay | GET | `/flight/timeline/{id}` | flight_recorder |
| Look up a timeline by gateway request_id | GET | `/flight/timeline/by-request/{request_id}` | flight_recorder |
| Fetch the signed receipt | GET | `/receipts/{execution_id}` | audit |
| Fetch the Merkle inclusion proof | GET | `/transparency/inclusion/{execution_id}` | audit |

Receipt and inclusion calls are fire-and-forget; the page renders the timeline before they return and patches them in when they arrive.

## Auto-refresh & realtime

- **List refresh**: every 30 seconds via `setInterval(fetchTimelines, 30_000)` at `ui/src/pages/FlightRecorder.jsx:67`.
- **Token-watcher**: a secondary interval at `:116` checks the auth-token freshness so the polling doesn't 401 silently after a token refresh.
- **No SSE subscription.** The Flight Recorder is poll-based; it never opens a Server-Sent Events stream of its own. (The Live Feed page is the SSE-driven view.)

## Per-agent scoping

Yes. The page reads `selectedAgentId` from the sidebar `AgentContext`. When set, every `flightService.listTimelines` call passes `params.agent_id` and only timelines for that agent are returned. Clearing the sidebar picker — or clicking the "Scope" chip — drops the filter.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| List empty for the chosen window | `No timelines in the window.` | Extend the time range; the public demo seeds traffic in bursts so quiet windows are normal. |
| Replay panel before any selection | Empty pane with hint text | Click a row in the list. |
| Receipt URL unavailable for a row | Receipt section blank in the panel | Older rows predate the receipts feature; expected. |

## Edge cases & known gotchas

- **403 from a write button**: not applicable on this page — Flight Recorder is read-only. If the page itself returns 401, the token has expired; the global auth handler will redirect to login.
- **Network error during fetch**: the timeline list shows a banner "Failed to load timelines" with a retry button. The 30-second poll continues regardless.
- **Stale token after suspend**: the token-watcher interval re-checks token expiry on every tick; a stale token triggers a silent refresh via the cookie path.
- **`/flight/timeline/{id}` returns 404**: the request_id may have predated the Flight Recorder service rollout; the backfill worker recovers these as `status="recovered_backfill"` but their step list will be empty.
- **Per-EC2 flap**: not applicable — all gateway proxy paths for `/flight/*` are catch-by-prefix in `services/gateway/main.py::proxy_flight`, so per-EC2 nginx-regex bugs do not bite this page.

## Related docs

- [Flight Recorder service](../../services/flight-recorder.md)
- [Audit service](../../services/audit.md) — receipt and transparency endpoints
- [Flow of a Decision](../../architecture/flow-of-a-decision.md) — the per-stage view this UI surfaces
- [10-Stage Pipeline](../../architecture/10-stage-pipeline.md) — the stages whose decisions show up in the replay

## Screenshot

![Flight Recorder](../_screenshots/flight-recorder.png)
