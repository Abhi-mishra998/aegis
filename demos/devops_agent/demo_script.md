# ACP DevOps Agent Governance — Live Demo Script

**Audience**: Platform engineers, SRE leads, security engineering teams  
**Duration**: 12–15 minutes (full), 6–7 minutes (executive)  
**Format**: Terminal + browser (Identity Graph UI at http://localhost:5173)

---

## Pre-demo checklist

- [ ] `cd acp && docker compose -f infra/docker-compose.yml up -d` (all 26 containers healthy)
- [ ] `python demos/devops_agent/setup_demo.py` (provisions agent + seeds graph)
- [ ] Browser open at http://localhost:5173 → Identity Graph page
- [ ] Terminal font size ≥ 18pt, dark theme
- [ ] Close Slack/notifications

---

## Framing (30 seconds)

> "Modern DevOps teams are deploying AI agents to automate Kubernetes operations — 
> scaling services, rotating secrets, patching configurations. These agents are fast 
> and tireless. They're also capable of catastrophic mistakes: deleting a production 
> namespace, escalating their own privileges, or running in a loop until the cluster 
> is gone.
> 
> ACP sits between the AI agent and your cluster. Every kubectl call goes through ACP 
> before touching infrastructure. Let me show you what that looks like."

---

## Scenario 1 — Safe Read Operations (1 min)

**Narration**:
> "The agent starts by reading cluster state — completely safe operations. Watch ACP 
> intercept each call and let them through."

**Action**: Run demo in terminal
```bash
.venv/bin/python demos/devops_agent/scripted_demo.py
```

**Points to emphasize**:
- `risk=0.000` on get/describe/logs operations
- ACP intercepts EVERY call — even reads are audited
- "The agent doesn't know it's talking to ACP — it looks like a normal cluster"

**Fallback**: If stack is down, add `ACP_DRY_RUN=1` prefix

---

## Scenario 2 — Safe Scaling (30 seconds)

**Narration**:
> "The agent wants to scale the staging payments-api from 1 to 2 replicas. This is 
> a legitimate operation. ACP evaluates the autonomy contract, checks the blast radius, 
> and allows it."

**Points to emphasize**:
- `risk=0.250` — moderate risk, not zero, because scaling can cause disruption
- Staging namespace is NOT production — contract allows it
- Contrast with Scenario 6 where the contract limit is hit

---

## Scenario 3 — Destructive Deletion DENIED (2 min) ⭐

**Narration**:
> "Now the agent tries to delete the production namespace. This would take down 
> payments, checkout, and auth simultaneously. Watch what happens."

**Expected output**:
```
$ kubectl delete namespace production
Error from server: ACP denied delete namespace/production
  HTTP Status : 403
  Action      : DENY
  Risk Score  : 0.950
  Findings    : [policy_deny]
  Decision    : DENIED — operation blocked before cluster execution
```

**Points to emphasize**:
- Risk=0.95 — maximum severity
- "Before cluster execution" — the cluster never even heard about this request
- Production namespace survived: `✓ Cluster state verified — production namespace intact`
- "Every denial is cryptographically signed and replayable"

**Fallback**: Show the Rego policy on screen
```bash
cat services/policy/policies/k8s_policy.rego | grep -A3 "HARD DENY"
```

---

## Scenario 4 — Privilege Escalation BLOCKED (1.5 min) ⭐

**Narration**:
> "Now something more subtle — the agent tries to grant itself cluster-admin. This is 
> a privilege escalation attack. Compromised agents often do exactly this before moving 
> laterally."

**Expected output**:
```
$ kubectl create clusterrolebinding devops-admin --clusterrole=cluster-admin ...
  ACP → DENY  risk=0.950  HTTP=403
  Findings    : [policy_deny, autonomy_denied_action]
```

**Points to emphasize**:
- Risk=0.95 — highest severity
- Two findings: OPA policy AND autonomy contract both independently deny
- "Defense in depth — OPA fires first, autonomy contract is a second layer"

---

## Scenario 5 — Blast Radius Visualization (2 min) ⭐⭐

**Narration**:
> "ACP doesn't just block — it shows you the blast radius. If this DevOps agent token 
> were stolen, what could an attacker reach?"

**Browser**: Navigate to Identity Graph → click DevOps Agent node → Blast Radius modal

**Points to emphasize**:
- 12 reachable nodes from the agent
- Critical assets highlighted in red: `stripe-api-key`, `payments-db-creds`, `admin-kubeconfig`
- Worst-case path: `agent → cluster-admin binding → kube-system → admin-kubeconfig → ALL`
- Risk score: `HIGH (0.847)`
- "This is computed before any incident — you know the blast radius at deploy time"

**Fallback**: The demo script prints a static blast radius table offline

---

## Scenario 6 — Autonomy Contract Enforcement (1 min)

**Narration**:
> "Autonomy contracts constrain what an agent can do in aggregate. This agent's 
> contract allows maximum 3 destructive operations per hour. Watch the 4th delete 
> get blocked."

**Points to emphasize**:
- First 3 pod deletes: allowed (staging pods don't trigger hard-deny)
- 4th delete: `autonomy.max_cost_exceeded` — contract quota hit
- "The agent isn't buggy — it just hit its safety envelope"

---

## Scenario 7 — Runaway Automation Defense (1 min)

**Narration**:
> "AI agents can enter runaway loops — deleting everything they can find. ACP detects 
> the behavioral pattern and throttles before infrastructure damage occurs."

**Points to emphasize**:
- `k8s_composite_risk` climbs with each operation
- Triggered detectors: `destructive_deletion_loop`, `pod_deletion_storm`, `automation_runaway`
- Rate limiting fires (HTTP 429 with `Retry-After`)
- "The behavioral signals feed into the risk score in real time"

---

## Scenario 8 — Kill Switch Persistence (1.5 min) ⭐⭐

**Narration**:
> "If you suspect a compromised agent, one call stops everything — across all operations, 
> for the entire tenant. And it survives a Redis FLUSHDB."

**Points to emphasize**:
1. Kill switch engaged: `POST /decision/kill-switch/{tenant_id}` → 200
2. Innocent read blocked: `HTTP 403` immediately
3. FLUSHDB: "Redis cache wiped — kill switch persists because it's written to Postgres"
4. Still blocked: `HTTP 403` after cache eviction
5. Disengage: `DELETE /decision/kill-switch/{tenant_id}` → operations resume

**Why it matters**:
> "In an incident, you can't trust the cache. ACP writes kill switch state to Postgres 
> first, so it survives restarts, FLUSHDB, pod evictions — anything."

---

## Scenario 9 — Cryptographic Receipts (1 min)

**Narration**:
> "Every ACP decision is cryptographically signed. Every denial is replayable with 
> full audit evidence. This is the governance guarantee."

```bash
acp verify-chain --base-url http://localhost:8000 --token $TOKEN --json
```

**Expected output**:
```json
{
  "valid": true,
  "processed": 47,
  "errors": 0
}
```

**Points to emphasize**:
- 47 events — every kubectl call we just ran is in the chain
- `is_integrous=true` — nothing was tampered with
- "Your compliance team can verify this offline, without trusting ACP"

---

## Closing (30 seconds)

> "What you just saw: a real AI agent performing real Kubernetes operations, governed 
> in real time by ACP. Safe operations got through instantly. Destructive operations 
> were denied before they touched the cluster. Behavioral anomalies were detected and 
> throttled. The kill switch stopped everything in under a second. And every single 
> decision is cryptographically proven.
>
> ACP doesn't slow down your DevOps automation — it makes it safe to run at scale."

---

## Fallback paths

| Issue | Recovery |
|-------|----------|
| Stack not healthy | `ACP_DRY_RUN=1 python demos/devops_agent/scripted_demo.py` |
| Graph UI blank | Show static blast-radius table printed by demo script |
| Kill switch scenario fails | Skip to Scenario 9 — "we'll circle back to kill switch after the call" |
| Auth 401 | Re-run `setup_demo.py` to get fresh credentials |
| High latency | Have `reports/sprint/` screenshots as fallback slides |

---

## Operator follow-up after demo

1. **Video recordings** (3 required — see README):
   - Quick prospect demo (5 min): Scenarios 1, 3, 4, 5
   - Technical deep dive (15 min): All 9 scenarios
   - Live demo dry run: Full run with narration

2. **Screenshots** (capture after live run):
   - Safe operation output (Scenario 1)
   - Denial output (Scenario 3 — shows risk score + findings)
   - Blast radius modal (Scenario 5 — Identity Graph UI)
   - Autonomy violation (Scenario 6)
   - Kill switch output (Scenario 8)

3. **Baselines to record**:
   - ACP decision latency p95 (target: <100ms)
   - Blast radius computation (target: <50ms for seeded graph)
   - Kill switch enforcement (target: <1s end-to-end)

4. **Wire the queue-age refresh loop** into gateway lifespan (noted in Sprint 3.5 
   operator follow-up — `asyncio.create_task` in `services/gateway/main.py:lifespan`).

---

## Platform-engineer narrative

**Why AI-driven DevOps automation is dangerous**:  
AI agents don't understand blast radius. A language model generating `kubectl delete` 
commands doesn't know that the production namespace contains $2M/day of payment 
processing. It doesn't know that a cluster-admin binding is an incident waiting to 
happen. It has no intuition about operational risk.

**How ACP governs cluster operations**:  
Every kubectl call is routed through a multi-phase pipeline before execution:
1. OPA policy evaluation — hard-deny rules for namespace/node/PV deletion, privilege escalation
2. Behavioral risk scoring — destructive loops, RBAC recon, secrets enumeration  
3. Autonomy contract enforcement — per-agent quotas on destructive ops per hour
4. Blast radius gating — high-risk operations above the blast-radius threshold are blocked

**How blast radius is precomputed**:  
The Identity Graph models every agent, service account, namespace, secret, and database 
as nodes with directed edges representing runtime relationships. BFS traversal from any 
agent node computes the reachable set under each compromise scenario (stolen token, 
prompt injection, malicious insider). The blast radius score is computed before any 
incident — operators know the risk surface at deploy time, not after.

**How ACP prevents catastrophic automation failures**:  
Three independent layers must all agree to allow a destructive operation:
1. OPA policy (deterministic, rule-based)
2. AI behavioral risk scoring (statistical, anomaly-based)
3. Autonomy contract (quota-based, per-agent SLA)

Any single layer can veto. The kill switch is a fourth layer — a tenant-level circuit 
breaker persisted in Postgres that blocks all operations regardless of other state.
