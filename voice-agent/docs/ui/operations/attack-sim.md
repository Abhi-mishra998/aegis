# Attack Sim

## What this page is for

Attack Sim is the extended catalog of attack scenarios beyond the 4 quick buttons on the Playground. It's the page for a security team that wants to *systematically* prove the platform blocks the categories of attack they care about: prompt injection variants, SQL injection variants, RCE attempts, k8s control-plane abuse, cross-tenant data access, credential theft, autonomy abuse. Each scenario is one click; results land back as a regular `Decision` envelope.

## Sidebar location & role gating

- **Sidebar group**: Operations dropdown.
- **Path**: `/attack-sim`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN` or `SECURITY`. Each scenario fires a `POST /execute` and is treated as a write operation. A `VIEWER` or `AUDITOR` opening the page sees the scenarios but every Run button returns the platform's 403.

## What you see

- **Header note** — short callout reminding the operator that every scenario fires a real `/execute` and produces a signed audit row. The denials are not synthetic; they are real platform behavior.
- **Agent picker** — at the top, driven by the sidebar `useAgents` selection.
- **Scenario grid** — cards grouped by category:
  - **Prompt Injection** — instruction-override, RAG-poisoning, jailbreak.
  - **SQL Injection** — `DROP TABLE`, `UNION SELECT` exfiltration, blind injection.
  - **RCE** — `rm -rf`, reverse shell, env-var exfil.
  - **K8s Abuse** — `delete namespace prod`, `create clusterrolebinding`, `exec into pod`.
  - **Cross-Tenant** — tenant id swap, customer data read across tenants.
  - **Credential Theft** — `cat /etc/shadow`, AWS metadata service exfil.
  - **Autonomy Abuse** — depth-3 delegation chain, time-window violation.
- **Result panel** — when a scenario is run, populates with the `Decision` envelope: action (always `deny` for the shipped scenarios), risk_score, rule_id, findings, audit_id.
- **"Run all" button** — fires every scenario in sequence; useful for a one-shot end-to-end audit.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Run a scenario | POST | `/execute` | gateway → decision pipeline |

(That's it — the page is a wrapper around `/execute` with pre-built payloads.)

## Auto-refresh & realtime

- **No auto-refresh.** Operator-driven.
- **The Live Feed page receives the SSE event for each scenario** as it runs; opening Live Feed in another tab shows the deny events arrive in real time.

## Per-agent scoping

Yes. The selected agent in the sidebar drives `agentId`. Without a selection, the page falls back to the first agent in the registry. Some scenarios are agent-class-specific (e.g. the k8s ones are most interesting when run against `devops-agent`).

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| No agent selected and no agents registered | `No agent selected. Register an agent in the Agent Registry and select it in the top bar before running simulations.` | Go to Agents → New Agent. |

## Edge cases & known gotchas

- **A scenario returns 200 `allow`**: a regression. The shipped scenarios should always deny. If one passes, file an issue with the request_id; either the rule has been weakened or a new bypass was introduced.
- **A scenario returns 504 `decision_timeout`**: the Decision pipeline exceeded the gateway's deadline. Retry; if persistent, inspect Settings → System Health.
- **"Run all" rate-limits halfway through**: the per-tenant rate limit can fire if all scenarios run in a tight burst. Wait a few seconds and re-run from the last failure.
- **Scenarios audit-log the same as real attacks**: they are real attacks against the platform's surface. The audit chain records them with the operator's user_id as the caller; the Audit Trail page shows them like any other deny.
- **Run-all is parallelizable but UI runs serially**: deliberate; serial output is easier to read.
- **Per-EC2 flap**: same as Playground — `/execute` is stable.

## Related docs

- [Playground UI](playground.md) — the smaller-scope sibling with 4 quick scenarios
- [Threat Scenarios](../../security/threat-scenarios.md) — the rules that block each category
- [Policy service](../../services/policy.md) — where the deny rules live
- [Decision service](../../services/decision.md) — the signal combiner

## Screenshot

![Attack Sim](../_screenshots/attack-sim.png)
