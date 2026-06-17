# Approval Inbox

*Where ESCALATE decisions wait for a human. The inbox queues every audit row marked `decision = escalate`, lets an authorised operator approve or reject once, and records the decision in the cryptographically-chained `human_override_events` table. Approved requests can be replayed by the SDK using a one-shot `X-Aegis-Approval-ID` ticket that expires after 5 minutes and is invalidated whenever policy changes.*

## What this page is for

This is the operator surface that closes the human-in-the-loop on every ESCALATEd request. The pipeline writes `decision=escalate` on actions a contract or escalation pattern flagged as "needs a human" — high-value wire transfers, cross-tenant access, anything the buyer wired to a `CFO`, `CISO`, or `SRE_LEAD` approver role. Until an operator acts, the calling SDK has the 202 `pending_approval` response in hand and the action has not been executed downstream.

The inbox is also the only place where the `approval` half of `human_override_events` is created. The mirror surface — recording an `override` of a hard `deny` — lives on the [Audit Trail](../primary/audit-trail.md) detail drawer. Both feed the same chained table.

## Sidebar location & role gating

- **Sidebar group**: advanced nav.
- **Path**: `/approval-inbox`.
- **Keyboard hint**: none.
- **Minimum role for read**: `AUDITOR` (or any caller authorised to read audit rows).
- **Approve / reject** requires `ADMIN` or `SECURITY`. The autonomy router resolves the acting identity from the gateway-injected `X-ACP-Actor` and `X-ACP-Role` headers (sourced from the validated JWT's `sub` and `role`); body-supplied fields are ignored on requests that flow through the gateway, so a browser cannot impersonate another operator.
- **Approver role enforcement**: an escalate row can stamp `metadata.approver_role` (e.g. `CFO`, `CISO`, `SRE_LEAD`). If the platform tenant has approver-role enforcement enabled, the autonomy service rejects the override unless the acting role matches the row's required `approver_role`.

## The end-to-end narrative

```
1. SDK fires a wire-transfer prompt → POST /v1/messages
   Gateway runs escalation_patterns.scan → matches "wire_transfer_large"
   Audit row written:
     decision               = escalate
     metadata.matched_pattern = wire_transfer_large
     metadata.approver_role = CFO
     metadata.policy_version = <current Redis value at decision time>
   SSE event llm_proxy_escalate is fanned out to subscribed browsers
   Response: HTTP 202 with body
     { status: "pending_approval", approval_id, approver_role, inbox_url }

2. Operator opens /approval-inbox
   The page polls auditService.searchLogs({decision: "escalate"}) every 8s
   and subtracts the request_ids that already appear in human_override_events
   so the queue only shows rows still waiting on a human.
   The amber pending-count badge on the sidebar entry reflects the same set.

3. Operator clicks Approve, enters a reason
   ("Treasury verified — invoice 2026-Q3-77") and submits.
   The UI calls POST /autonomy/overrides via the autonomy proxy.
   Backend writes a human_override_events row and publishes a per-tenant
   approval_resolved SSE event. Every browser viewing the inbox sees the
   row clear in real time; the same event drives the badge decrement.

4. SDK replays the same prompt with the approval ticket attached:
   POST /v1/messages
   X-Aegis-Approval-ID: <approval_id from step 1>

   Gateway's lookup_approval helper resolves the ticket against:
     - the original escalate audit row (the source of truth)
     - the matching human_override_events row (proof of approval)

   Two gates run before the replay shortcut is allowed:

   Gate 1 — TTL freshness
     (now - decided_at) < APPROVAL_REPLAY_TTL_S (300 seconds).
     An expired ticket falls through to a fresh deny/escalate scan.

   Gate 2 — Policy version stability
     escalate_row.metadata.policy_version == current Redis value of
     acp:tenant:policy_version:{tenant_id}.
     A mismatch falls through to a fresh deny/escalate scan.

   Both gates passing → the replay shortcut bypasses the escalation
   pattern engine and the request is forwarded to Anthropic.
   Response: HTTP 200 with the real model reply.
```

Live evidence (cross-arg replay path validated end-to-end against the prod-ha deployment): override returned `200`, the override row showed `status=approved`, and the SDK replay completed with `HTTP 200` in 1.2 s.

## The two security gates

The replay shortcut is a security boundary, not a convenience. Two independent gates protect it.

### Gate 1 — Replay TTL (`APPROVAL_REPLAY_TTL_S = 300`)

Constant defined at `services/gateway/proxy_helpers.py`. Once an operator approves a ticket, the SDK has five minutes to replay it. After 300 seconds elapsed since `decided_at`, `lookup_approval` returns `None` and the SDK runs the full deny/escalate scan again — exactly as if the approval had never happened.

Why the cap exists:

- **No "approved forever" tickets.** A stolen approval token cannot be parked indefinitely.
- **Forces fresh policy evaluation on long-lived sessions.** An approval given at 09:00 cannot bypass a policy that was tightened at 15:00 — even if the SDK only replays at 15:30.
- **Caps cross-process leakage.** Even if a ticket leaks into the wrong process (logs, another worker), the blast radius is bounded to a five-minute window.

The constant is centralised. Tightening the platform-wide TTL is a single-value change at one location.

### Gate 2 — Policy version invalidation (`acp:tenant:policy_version:{tenant_id}`)

Every `POST /policy/upload` (see `services/gateway/routers/policy.py:upload_policy_proxy`) increments the per-tenant Redis key `acp:tenant:policy_version:{tenant_id}`. At the moment the escalate audit row is written, the gateway stamps the *then-current* value into `metadata.policy_version`. The replay path compares the stamped value to the current Redis value:

- **Match** → the policy world is the same; the replay shortcut continues.
- **Mismatch** → policy changed between the 202 and the replay; the shortcut falls through to a fresh deny/escalate scan against the *new* policy.

> **If an operator tightens a policy between the 202 and the replay, the replay falls through to the new policy. This is intentional — old approvals do not bypass tightened controls.**

The flip side is also true: if a policy was loosened between the approval and the replay, the request still gets re-evaluated. The platform never trusts a stale approval to override the current policy state, in either direction.

**Fail-closed on Redis error.** If the Redis lookup of `acp:tenant:policy_version:{tenant_id}` raises (network blip, key eviction, cluster failover), `lookup_approval` returns `None` and the request goes through the full deny/escalate scan. There is no "Redis unavailable → assume the policy hasn't changed" branch. The replay shortcut is an optimisation; the fresh scan is the fallback.

Both gates run on every replay attempt. The shortcut is taken only if both gates pass.

## What you see

- **Header bar** — page title (`Inbox` icon), window selector (Last 1h / 24h / 7d / 30d, default 24h), Refresh button.
- **Pending pane (left)** — every escalated audit row in the window with no matching `approval` or `override` event yet. Each row shows tool, the request-id prefix, decision timestamp, and a severity badge derived from `metadata_json.risk_score`:

  | Risk band | Badge |
  |---|---|
  | ≥ 0.85 | `CRITICAL` (rose) |
  | ≥ 0.60 | `HIGH` (amber) |
  | ≥ 0.30 | `MEDIUM` (sky) |
  | < 0.30 | `LOW` (neutral) |

- **Recently resolved pane** — last 25 escalations from the window that already have a recorded `approval` or `override`. Read-only.
- **Detail card (right)** — when a row is selected, shows:
  - `request_id` and decision timestamp.
  - Agent, tool, decision, action.
  - Risk score and the canonical findings vocabulary (`metadata_json.findings`).
  - The pipeline's `reason` for the escalation.
  - Full request metadata as collapsed JSON.
  - **Operator note** textarea (recorded with the override).
  - **Approve** / **Reject** buttons.
- **Status banners** — green on success, rose on error, both inline above the panes.
- **Empty state** — `No pending approvals. Either no ESCALATE in the window, or everything's been actioned.`

## The approve / reject contract

Both buttons hit the same backend endpoint (`POST /autonomy/overrides`) but with a different `event_type`:

| Button | `event_type` | What it means downstream |
|---|---|---|
| Approve | `approval` | The SDK may replay the request with `X-Aegis-Approval-ID: <approval_id>`. Subject to TTL + policy-version gates. |
| Reject  | `override`  | The decision stands as ESCALATE; the action does not run. The reject is itself audited. |

The reason field is required by the autonomy schema — the UI auto-fills a stock reason ("Operator approved" / "Operator rejected") if the operator submits blank, but a free-text note is strongly encouraged because it lands in the durable audit chain alongside the override row.

The acting operator's identity is **not** taken from the form. It is read from the gateway-injected `X-ACP-Actor` and `X-ACP-Role` headers — both populated from the validated JWT. The browser cannot impersonate another operator.

## Real-time updates

Three triggers refresh the inbox:

1. **Initial fetch on mount.** Pulls escalated rows and human-override events in parallel.
2. **8-second poll loop** of `auditService.searchLogs({decision: "escalate"})`. Cheap because the gateway has audit-list pagination + filter pushdown and the response is small.
3. **Per-tenant SSE channel** subscribing to `approval_resolved`. The event fires on every `POST /autonomy/overrides` (source: `services/gateway/routers/autonomy.py:create_override`) and includes the `request_id` of the escalation that just got actioned. The page handles it by removing the row from `pending` and dropping it into `resolved` without a full refetch — a second operator approving on another browser sees the queue clear instantly.

The sidebar's pending-count badge tracks the same union: pending escalations minus resolved ones. The badge turns amber when the count is greater than zero so an operator scrolling past doesn't miss the queue.

## The `X-Aegis-Approval-ID` header contract

After an approval lands, the SDK replays the same prompt with one extra header:

```
X-Aegis-Approval-ID: <approval_id from the 202 body>
```

What the gateway does with it:

1. **Look up the audit row** by `approval_id`. If it's absent or its `decision` is not `escalate`, the header is treated as if it were not present — the request runs the full pipeline.
2. **Look up the override** in `human_override_events` for the same `request_id`. If the most recent event is not an `approval`, the shortcut is not taken.
3. **Run Gate 1 (TTL)** and **Gate 2 (policy version)**. Either gate failing skips the shortcut.
4. **Take the shortcut** only if all three lookups + both gates pass. Otherwise the request is re-evaluated.

The header is **single-shot in intent**. The TTL caps how long it can be re-used; replaying it five times in 30 seconds is permitted by the platform but discouraged because every reuse opens a slim window where policy could have changed between calls.

The `aegis-anthropic` SDK package implements this header automatically: on a `202 pending_approval` response, the SDK stores the `approval_id`; on a subsequent retry of the same prompt, it attaches the header without the caller writing a line of code. Same surface for `aegis-openai` and `aegis-bedrock`. Direct curl users can attach the header themselves — see [API Reference](../../api/reference.md) for the wire shape.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List pending escalations | GET | `/audit/logs?decision=escalate&limit=200` | audit |
| List human-override events | GET | `/autonomy/overrides?minutes={n}&limit=500` | autonomy |
| Approve / reject | POST | `/autonomy/overrides` | autonomy |
| Realtime "row cleared" event | GET (SSE) | `/events/stream?token=…` (channel `approval_resolved`) | gateway |
| Replay with approval | POST | `/v1/messages` (header `X-Aegis-Approval-ID: <id>`) | gateway |

## Per-agent scoping

No. The inbox is tenant-scoped — every operator with `ADMIN` or `SECURITY` on the tenant sees every pending approval. Filtering by agent is on the roadmap but not yet shipped; the [Audit Trail](../primary/audit-trail.md) page is the workaround when you need to filter to a single agent before deciding.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No escalations in the window | `No pending approvals. Either no ESCALATE in the window, or everything's been actioned.` | Healthy. Either nothing was escalated, or every escalation was actioned. |
| Pending row but no row selected | `Pick a pending approval on the left.` | Click any row in the pending pane. |
| Override POST failed (network) | rose banner with the SDK error message | Retry. The form does not clear so the operator note isn't lost. |

## Edge cases & known gotchas

- **Approval is APPEND, not REWRITE.** The original escalate audit row keeps its ESCALATE outcome. The approval is a separate `human_override_events` row chained into the same ed25519 log. The signed receipt for the escalation still verifies; Sprint-1 chain verification still passes.
- **Reject is also auditable.** A reject records `event_type=override` with the operator's reason. The receipt later proves a human looked at the decision and chose not to allow it — the absence of a downstream call is not silence.
- **TTL is measured from `decided_at`, not from when the SDK started waiting.** A 4-minute-58-second-old approval can still be replayed once. The next replay (a couple of seconds later) will fail Gate 1.
- **Policy upload increments globally for the tenant.** Tightening any rule (including unrelated ones) bumps `acp:tenant:policy_version:{tenant_id}`, which invalidates every outstanding approval ticket in that tenant. This is intentional — the platform cannot tell which rule change matters for which pending action, so it errs on the side of re-evaluation.
- **Two operators acting at the same time.** The SSE `approval_resolved` event lands within ~100 ms of the `POST /autonomy/overrides` returning, so the second operator's UI removes the row before they can double-action. If they do race the round-trip, the second `POST /autonomy/overrides` succeeds (the table allows multiple events per request_id), but the SDK's replay path picks up the most recent `approval` event and the action is only granted once at the wire.
- **Severity badge derives from the audit row's stamped `risk_score`.** Re-running the same prompt after a model change may produce a different risk score; the inbox surfaces the score as it was at the time of escalation, not the live recomputation.
- **No agent filter yet.** Tracked; for now use the [Audit Trail](../primary/audit-trail.md) page if you need per-agent filtering.

## Related docs

- [Autonomy service](../../services/autonomy.md) — the home of `/autonomy/overrides`, contracts, and playbooks.
- [Audit Trail](../primary/audit-trail.md) — the durable record where each escalate row lives.
- [Live Feed](live-feed.md) — the broader SSE surface; the `llm_proxy_escalate` and `approval_resolved` events both surface there too.
- [Gateway service](../../services/gateway.md) — the home of `lookup_approval`, `escalation_patterns.scan`, and the policy-version Redis key.
- [Policies UI](../primary/policies.md) — uploading or tightening a policy here is exactly what bumps `acp:tenant:policy_version:{tenant_id}` and invalidates outstanding approvals.
- [Flow of a Decision](../../architecture/flow-of-a-decision.md) — where the ESCALATE outcome originates in the 10-stage pipeline.
- [API Reference](../../api/reference.md) — the wire shape of `POST /autonomy/overrides` and the `X-Aegis-Approval-ID` header.
- [SDK Wrappers](../../integrations/sdk-wrappers.md) — `aegis-anthropic`, `aegis-openai`, `aegis-bedrock` all carry the approval-replay flow.

## Screenshot

![Approval Inbox](../_screenshots/approval-inbox.png)
