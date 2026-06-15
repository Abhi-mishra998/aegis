# Live Demo

> Real LLM agent (Groq `llama-3.3-70b`) tries to complete an engineering
> task. Every tool call it proposes goes through the actual Aegis pipeline.
> The dangerous ones are blocked. The whole trace lands on the audit chain
> as signed receipts. This is the page operators show on the first
> customer call — no slides, no fake data.

## What this page is for

This page exists so a prospect can *watch* the platform stop an autonomous
agent in real time. They type a task, click run, and 30 seconds later they
see:

- The LLM's intended actions (what Groq decided the agent should do).
- The Aegis decision per action (`allow` / `deny` / `escalate`), with the
  per-step risk score, behavior findings, and matched policy rule.
- The audit chain growing in real time, each row ed25519-signed and
  chained via `prev_hash → event_hash`.

The denial is not theatrical — it comes from the actual OPA rule
`critical_destructive_deny.rego` running against the demo agent's
`risk_level=critical` posture. Different agents with different risk levels
produce different decisions on the same call. The customer can verify this
by opening Decision Explorer with any `request_id` from the trace.

## Sidebar location & role gating

- **Sidebar group**: Primary nav (first item).
- **Path**: `/live-demo`.
- **Keyboard hint**: `G X`.
- **Minimum role**: `ADMIN`. The page provisions a critical-risk agent on
  first run, which requires write access to the registry.

## What you see

- **Header** — operator email + the model used (`llama-3.3-70b`).
- **Scenario picker** (added 2026-06-13 in R5) — three scripted scenarios
  along the top: **fintech_data_egress**, **devops_destruction**, **support_pii_exfil**.
  Each scenario sets the demo agent's `risk_level` and seeds a domain-specific
  prompt + a curated mix of tool calls that exercise different OPA rules.
  This lets the operator demo three distinct stories on one click — a
  payments agent (PCI deny on bulk row export), a DevOps agent (k8s
  prod-namespace deny), and a support agent (PII redaction on outbound
  email). Backed by `GET /demo/scenarios` and `POST /demo/groq-agent` with
  the `scenario` parameter.
- **Prompt panel** — multi-line textarea pre-filled by the selected
  scenario. Operators can still edit it before clicking Run. The Run button
  is disabled while a run is in flight.
- **Summary tiles** — 4 KPI tiles (Allowed / Blocked / Escalated / Errors)
  that count up as each step animates in. Counts are recomputed against
  the already-revealed slice so the numbers track the visible trace.
- **Pipeline trace** — one card per tool call, with the tool icon, the
  decision badge (green / rose / amber), risk score, latency in ms, the
  payload Groq proposed (truncated to 60 chars per field), findings
  surfaced by the decision engine, and the `request_id` deep-link target.
  Steps reveal one at a time at ~700 ms apart.
- **Audit chain side rail** — last 8 audit rows, refreshing after each
  run. Each row shows action + tool, decision badge, the short event hash,
  and the timestamp. The footer line reminds the viewer that every row is
  ed25519-signed and chained by prev_hash → event_hash.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| List the three scripted scenarios (R5) | GET | `/demo/scenarios` | gateway (`services/gateway/routers/demo.py`) |
| Run a demo prompt under a scenario | POST | `/demo/groq-agent` with body `{prompt, scenario}` (`scenario` ∈ `fintech_data_egress`, `devops_destruction`, `support_pii_exfil`, defaults to legacy critical-only path) | gateway |
| Refresh the audit-chain side rail | GET | `/audit/logs?limit=8` | audit |

The `POST /demo/groq-agent` route:

1. Reads the operator's tenant + JWT from the existing cookie / bearer.
2. If `scenario` is set: provisions / looks up the matching scenario agent
   (`aegis-demo-fintech`, `aegis-demo-devops`, `aegis-demo-support`) with
   the scenario's `risk_level`. If `scenario` is absent: falls back to the
   original `demo-groq-agent-v3` agent at `risk_level=critical`.
3. Calls Groq server-side with the scenario's system prompt to produce a
   tool-call plan. The `GROQ_API_KEY` lives in the gateway env only — the
   browser never sees it.
4. For each tool call, posts to the gateway's own `/execute` endpoint so
   the trace goes through the real policy → behavior → decision →
   autonomy pipeline.
5. Returns one JSON payload with the full trace plus the scenario id so
   the UI can label the run; the UI animates it client-side.

## Auto-refresh & realtime

- The trace is not SSE — the route returns one payload with all steps,
  and the UI animates them in. Simpler to ship, faster to extend; an SSE
  variant can replace this without changing the page shape.
- The audit chain side rail re-fetches after each run completes.

## Sample prompts

The page seeds five chips that reliably produce a mixed allow/deny trace:

- *"Summarize the README, then clean up old logs in /var/log to free disk."*
- *"Pull the latest user list from the production DB and email it to devs."*
- *"Roll out the new payment policy then restart the worker fleet."*
- *"Investigate the 5xx spike — check container logs and recent deploys."*
- *"Backup customer table to S3 then drop the old shadow_users table."*

The first three trigger denials on `tool.shell` or `tool.sql_query`. The
fourth is mostly allows (read-only investigation). The fifth produces a
classic mix — backup is fine, DROP is blocked.

## Per-agent scoping

Each scenario provisions its own demo agent with a scripted risk level:

| Scenario | Agent name | Risk level | Story |
|---|---|---|---|
| `fintech_data_egress` | `aegis-demo-fintech` | `medium` | Payments agent. Allowed reads under the medium PII row threshold (1000); denied on bulk PII export above it via `action_semantics_deny.rego._pii_row_threshold_breached`. |
| `devops_destruction` | `aegis-demo-devops` | `low` | DevOps agent. Allowed on dev/test namespaces; denied on prod/staging via `_k8s_prod_destruction`. |
| `support_pii_exfil` | `aegis-demo-support` | `medium` | Support agent. Allowed internal reads; denied on outbound email containing PII to an external domain via `_external_exfil`. |
| (legacy default) | `demo-groq-agent-v3` | `critical` | Original blanket-critical agent. Used when no `scenario` is sent. Critical-risk agents trip `critical_destructive_deny.rego` on any destructive tool name. |

To demo a deliberately different outcome, switch scenarios in the picker —
or open `/agents`, edit the underlying scenario agent's `risk_level`, then
rerun. The R0 + v3-deep rules in `action_semantics_deny.rego` scale the PII
row threshold off `risk_level`, so the same prompt produces different
decisions across risk levels.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No run yet | "No demo run yet" | Click Run live demo |
| Groq returned no parseable plan | "Groq returned no usable tool-call list" | Tweak the prompt; aggressive prompts produce richer plans |
| `GROQ_API_KEY` unset on the gateway | 503: "GROQ_API_KEY is not configured" | See deployment doc — set on instance via SSM, restart gateway |

## Edge cases & known gotchas

- **All-allow trace** — the demo agent's `risk_level` was reset to `medium`
  or `low`. The OPA rule
  `services/policy/policies/critical_destructive_deny.rego` only fires
  for `critical`. Open `/agents`, set the demo agent back to `critical`,
  rerun.
- **Empty step list** — Groq returned an object without a `tools` key. The
  route tries the common variants (`tools`, `tool_calls`, `calls`,
  `actions`) before falling back to regex extraction. If you see this
  persistently, check the system prompt format and the Groq model.
- **Slow first call** — the model is cold; subsequent calls are ~600 ms.
- **Tool name alias** — Groq sometimes returns bare `shell` instead of
  `tool.shell`. The route folds these back to the canonical names via the
  `_TOOL_ALIASES` map so the trace never shows `unknown`.
- **Soft-deleted agent** — if the demo agent was deleted via the registry
  API, the search now skips `terminated` / `deleted` / `quarantined`
  rows so a fresh ACTIVE one gets provisioned cleanly.

## Related docs

- [Gateway service](../../services/gateway.md) — hosts the `/demo`
  router.
- [OPA Policies](../../security/opa-policies.md) — covers the new
  `critical_destructive_deny.rego` rule the demo relies on.
- [Cryptographic Audit Chain](../../security/crypto-audit-chain.md) —
  the chain proof the side rail surfaces.
- [Audit Trail](audit-trail.md) — open this after a demo run to filter
  by `decision=deny` and inspect the receipts.
- [Decision Explorer](../../architecture/decision-explorer.md) — paste any
  `request_id` from the trace to see the stage-level span graph.
