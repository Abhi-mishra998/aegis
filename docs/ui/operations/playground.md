# Playground

## What this page is for

The Playground is the safe sandbox for trying things against the live decision pipeline. An author picks an agent, picks a tool from that agent's allow-list, supplies a payload, and clicks Run. The request goes through the same gateway, the same policy stage, the same decision engine — and returns the same `Decision` envelope an SDK would receive. The Playground is also where the 4 shipped Attack Scenarios live as one-click buttons that fire known-malicious payloads to demonstrate the block path.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/playground`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN` or `SECURITY` — `/execute` is a write path. A `VIEWER` or `AUDITOR` opening the page sees the agents picker and the payload editor but every Run button returns the platform's 403 explaining the role requirement.

## What you see

- **Agent picker** — dropdown of agents in the tenant. Drives the rest of the page.
- **Tool dropdown** — populated by `/agents/{id}/permissions` filtered to `ALLOW`. Auto-selects the first safe-looking tool (skips names containing `delete`, `drop`, `exec`, `kill`).
- **Payload editor** — JSON editor with realistic auto-fill keyed off the selected tool from the platform's 45-entry `TOOL_PAYLOADS` table.
- **Attack Scenario cards** — 4 buttons at the top:
  - **PII Bulk Export** — `crm.bulk_export` with PII fields requested.
  - **RCE via `rm -rf`** — `shell.exec` with destructive payload.
  - **SQL Injection** — `db.query` with `DROP TABLE`.
  - **K8s Production Namespace Delete** — `k8s.delete.namespace` against `prod`.
- **Run button** — fires `POST /execute`. Loading spinner during the gateway round-trip.
- **Result card** — full `Decision` envelope: action, risk_score, findings, signals_evaluated, the receipt URL, the audit_id. On deny (403), the same card renders with `decision="deny"` and `risk=1.0` styling.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Load the agent's tool permissions | GET | `/agents/{id}/permissions` | registry |
| Execute the tool | POST | `/execute` | gateway → decision pipeline |

## Auto-refresh & realtime

- **No auto-refresh.** The page is operator-driven; nothing polls.
- **The result event also lands on the SSE Live Feed** because every `/execute` decision publishes to `acp:sse:tenant:{tenant_id}` and `acp:sse:agent:{agent_id}`. Open Live Feed in another tab to see the same event from a different angle.

## Per-agent scoping

Yes — the page is intrinsically agent-scoped. The sidebar `useAgents` selection seeds the agent picker; changing the picker reloads the tool dropdown. The `lastLoadedAgentRef` guard prevents a stale-race when the user clicks quickly across agents.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No agents registered for the tenant | `No agents registered. Create an agent first.` | Go to Agents → New Agent. |
| Selected agent has no ALLOW tools | `No tools in agent allow-list — type a tool to test deny` | Type any tool name; the request will deny at stage 4. Useful for testing policy. |
| Page just loaded, no run yet | `No execution yet` | Click Run. |

## Edge cases & known gotchas

- **403 on Run**: caller is `VIEWER` or `AUDITOR`. Re-login with `ADMIN` or `SECURITY`. The `Decision` envelope is NOT returned to a denied write — only the 403 with the rule explanation.
- **Attack scenarios return 403 with `policy_denied`**: this is the *correct* outcome. The card styles the deny as red and highlights the rule_id and findings. The platform proved it can block the attack.
- **Allowed `db.query` returns mock data**: the demo deployment's `db.query` proxies to a stub that returns canned rows; production deployments wire a real tool target.
- **Audit row written even on deny**: the request_id and audit_id are in the result; click through to Audit Trail to verify the signed row.
- **Payload editor JSON-validates before send**: malformed JSON shows an inline error rather than firing a 422.
- **Per-EC2 flap**: `/execute` proxy is stable. The agents permission GET goes through `/agents/{id}/permissions` which is also stable after the SPA-vs-API nginx fix.

## Related docs

- [Gateway service](../../services/gateway.md) — the receiver of the `/execute` call
- [Decision service](../../services/decision.md) — the signal combiner that produces the `Decision` envelope
- [Policy service](../../services/policy.md) — the upstream that decides allow vs deny
- [Attack Sim UI](attack-sim.md) — extended catalog beyond the 4 Playground scenarios
- [Threat Scenarios](../../security/threat-scenarios.md) — the rules that block the shipped attack cases
- [Flow of a Decision](../../architecture/flow-of-a-decision.md) — the end-to-end trace of one Playground Run

## Screenshot

![Playground](../_screenshots/playground.png)
