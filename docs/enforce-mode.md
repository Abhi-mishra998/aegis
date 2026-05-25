# Upgrading from Observe to Enforce Mode

ACP ships with `OPA_FAIL_MODE=open` recommended for initial evaluation. In open mode, the
policy engine never blocks a call — it evaluates and records but returns `allow` on any
policy evaluation error or timeout. The risk engine still scores every call; those scores
appear in the audit log but do not cause rejections.

Enforce mode (`OPA_FAIL_MODE=closed`) activates the full security pipeline:
- Calls with risk score ≥ 0.70 return `403 Forbidden` to the agent.
- Calls that violate OPA policies are blocked regardless of risk score.
- Policy evaluation timeouts fail closed (blocked, not allowed).

---

## Before You Switch

Do these checks first. Switching to enforce mode on a live agent without them causes
unexpected 403s.

### 1. Review recent audit logs for high-risk calls

```bash
# Any calls with risk_score >= 0.5 in the last 24 hours
curl -s "http://localhost:8000/audit/logs?limit=100" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  | jq '[.data.items[] | select(.risk_score >= 0.5)] | sort_by(.risk_score) | reverse | .[:10] | .[] | {created_at, tool_name, risk_score, findings}'
```

Anything with `risk_score >= 0.70` will be blocked in enforce mode. Investigate each one:
- Is it a real threat? Leave it — it should be blocked.
- Is it a false positive? Tune your policy or lower the signal weight.

### 2. Check what your policies currently do

```bash
# List active policy rules
curl -s http://localhost:8000/policy/rules \
  -H "Authorization: Bearer $TOKEN" \
  | jq '.data[] | {tool_name, action, condition}'
```

### 3. Verify OPA is running and healthy

```bash
curl -s http://localhost:8000/system/health | jq '.services.policy'
# Expected: {"status": "healthy", ...}
```

If OPA is unhealthy and you switch to `OPA_FAIL_MODE=closed`, all tool calls will be blocked
until OPA recovers. Confirm OPA is stable before proceeding.

### 4. Verify the audit chain is intact

```bash
.venv/bin/acp verify-chain \
  --tenant $TENANT \
  --gateway http://localhost:8000 \
  --token "$TOKEN"
# Must print: Chain OK — N records verified, 0 violations
```

---

## Switching Modes

### Docker Compose deployment

```bash
cd infra

# Edit .env: change OPA_FAIL_MODE
sed -i.bak 's/OPA_FAIL_MODE=open/OPA_FAIL_MODE=closed/' .env

# Restart affected services (rolling — no downtime if replicas > 1)
docker compose up -d --no-deps gateway decision policy

# Verify the setting took effect
docker exec acp_gateway sh -c 'echo $OPA_FAIL_MODE'
# Expected: closed
```

### Kubernetes / Helm deployment

```bash
# In values.yaml or values.prod.yaml:
# global:
#   env:
#     OPA_FAIL_MODE: closed

helm upgrade acp ./infra/helm/acp \
  -f infra/helm/acp/values.yaml \
  -f infra/helm/acp/values.prod.yaml \
  --namespace acp \
  --set global.env.OPA_FAIL_MODE=closed

# Verify rollout
kubectl rollout status deployment/acp-gateway -n acp
kubectl rollout status deployment/acp-decision -n acp
kubectl rollout status deployment/acp-policy -n acp
```

### Environment variable (any deployment)

```bash
# The single env var that controls the entire policy enforcement mode:
OPA_FAIL_MODE=closed   # enforce: block on policy error / timeout
OPA_FAIL_MODE=open     # observe: allow on policy error / timeout (default for evaluation)
```

---

## Verification After Switching

Run a test call that should be blocked:

```bash
# This call uses a tool not in the agent's allow-list — should return 403
curl -s -X POST http://localhost:8000/execute \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "'"$AID"'",
    "tool_name": "shell.exec",
    "parameters": {"command": "rm -rf /"},
    "context": {}
  }' | jq '{decision: .decision, error: .error}'
# Expected: {"decision": "block", "error": "tool_not_permitted"}
```

Run a call that should be allowed:

```bash
curl -s -X POST http://localhost:8000/execute \
  -H "Authorization: Bearer $AGENT_TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "'"$AID"'",
    "tool_name": "read_file",
    "parameters": {"path": "/data/report.csv"},
    "context": {}
  }' | jq '.decision'
# Expected: "allow"
```

Check that the policy service is failing closed on timeouts:

```bash
curl -s http://localhost:8000/system/health | jq '.services.policy.opa_fail_mode'
# Expected: "closed"
```

---

## Risk Score Thresholds

These are the default decision boundaries in enforce mode. They are not configurable via env
var today — change them in `services/decision/engine.py` and rebuild.

| Risk Score | Action | HTTP Status |
|-----------|--------|-------------|
| < 0.30 | ALLOW | 200 |
| 0.30–0.49 | MONITOR (allow + flag) | 200 |
| 0.50–0.69 | THROTTLE (allow + rate limit) | 200 |
| 0.70–0.89 | ESCALATE → block | 403 |
| ≥ 0.90 | KILL → block | 403 |

A policy `deny` rule overrides the risk score: calls that violate OPA policy are always
blocked regardless of score.

---

## Gradual Rollout Strategy

If you have production traffic and want to minimize blast radius:

**1. Per-tenant enforcement (recommended)**

Enforce on a single low-risk tenant first. All other tenants remain in observe mode.

Currently, `OPA_FAIL_MODE` is global. To enforce per-tenant, set a custom policy rule
that hard-allows all tools for specific tenants while enforcing for others:

```rego
# services/policy/policies/agent_policy.rego
default allow = false

# Allow all tools for tenant in observe mode
allow {
    input.tenant_id == "00000000-0000-0000-0000-000000000002"  # pilot tenant
}

allow {
    input.tool_name == data.agents[input.agent_id].permissions[_].tool_name
}
```

**2. Use the kill switch for rapid rollback**

If enforce mode causes unexpected production blocks, the kill switch halts all enforcement
for a tenant within one second — without restarting services:

```bash
# Activate kill switch for a tenant (emergency rollback)
curl -s -X POST http://localhost:8000/decision/kill-switch \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "reason": "Rollback during enforce-mode pilot"}'

# Deactivate
curl -s -X POST http://localhost:8000/decision/kill-switch \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

Every kill switch activation is recorded in the audit log.

---

## Rollback to Observe Mode

```bash
# Docker Compose
cd infra
sed -i.bak 's/OPA_FAIL_MODE=closed/OPA_FAIL_MODE=open/' .env
docker compose up -d --no-deps gateway decision policy

# Kubernetes
helm upgrade acp ./infra/helm/acp --namespace acp \
  --set global.env.OPA_FAIL_MODE=open --reuse-values
```

Rollback takes effect on the next request after the pod restarts (~15 seconds). Audit
history is never affected — all decisions from enforce mode remain in the immutable log.
