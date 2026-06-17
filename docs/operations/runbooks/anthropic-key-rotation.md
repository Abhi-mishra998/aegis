# Runbook: Anthropic API Key Rotation

## Scope

Rotate the `ANTHROPIC_API_KEY` used by the gateway LLM router (`services/gateway/llm_router.py`) and any wizard-driven agent registrations (`services/registry/wizard.py:394`). This is the upstream Claude API credential — distinct from internal signing keys covered by [Key Rotation](../key-rotation.md).

## Severity / cadence

- **Routine rotation:** 90 days.
- **Emergency rotation:** Immediately if the key is suspected leaked (commit accident, CI log dump, departing employee with access).
- **Drill cadence:** 30 days — verify the rotation procedure runs end-to-end without disrupting in-flight traffic.

## Oncall + paging

| Channel | Where |
|---|---|
| Slack | `#aegis-incidents` (announce rotation start + finish) |
| PagerDuty | Only paged if emergency rotation; routine rotations are planned |

## Dashboards

- **Grafana → ACP Operations** (`acp-ops`) → "Behavior fail-CLOSED rate" and per-service p95 panels. LLM router upstream failures show up as 5xx on the gateway.
- **Grafana → ACP Tenant Activity** (`acp-tenant-activity`) → "Inference cost blocked" panel; spikes after rotation usually mean a service still has the old key cached.

## Prerequisites

- AWS SSM Parameter Store write access for `/aegis-gateway/anthropic-api-key` (or whichever parameter your deployment uses).
- The new Anthropic key, generated from the Anthropic console (`console.anthropic.com → Settings → API Keys`) by an account with the right organization permissions.
- SSH access to both ASG hosts (or `aws ssm send-command` permission to target the ASG).

## Rotation steps

### 1. Generate the new key

In the Anthropic console, create a key labelled `aegis-prod-YYYYMMDD`. Do NOT revoke the previous key yet.

### 2. Push the new key into SSM

```bash
aws ssm put-parameter \
  --region ap-south-1 \
  --name /aegis-gateway/anthropic-api-key \
  --type SecureString \
  --value "$NEW_KEY" \
  --overwrite
```

The audit container's IAM role needs `ssm:GetParameter` on the parameter ARN and `kms:Decrypt` on the encrypting KMS key.

### 3. Restart the gateway on both hosts

The gateway re-reads SSM at container startup; there is no in-process refresh path today.

```bash
INSTANCE_IDS=$(aws autoscaling describe-auto-scaling-groups \
  --region ap-south-1 \
  --auto-scaling-group-names acp-prodha-asg \
  | jq -r '.AutoScalingGroups[0].Instances[].InstanceId' | paste -sd ' ' -)

aws ssm send-command \
  --region ap-south-1 \
  --instance-ids $INSTANCE_IDS \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker restart acp_gateway"]'
```

Restart is sequential per host — the ALB will drain connections from the restarting host while the other serves traffic. No customer-visible downtime if both hosts are healthy at start.

### 4. Verify the new key is active

```bash
# A test inference through any agent that routes via Anthropic should succeed
curl -sS -X POST https://aegisagent.in/execute \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "X-Agent-ID: $AGENT_ID" \
  -H "Content-Type: application/json" \
  -d '{"tool_name":"llm.complete","payload":{"prompt":"ping"}}'

# Expected: 200 with a decision envelope; the audit row's metadata
# carries the upstream provider's request id.
```

If you see `401` or `invalid_api_key` in the gateway logs, the SSM read failed or the container still has the old key. Re-check by exec-ing into the container:

```bash
docker exec acp_gateway env | grep ANTHROPIC_API_KEY | head -c 16
```

The first 16 characters should match the new key's prefix (`sk-ant-`).

### 5. Revoke the old key

After confirming traffic flows through the new key, return to the Anthropic console and revoke the previous key. Once revoked, any service still pointing at the old key will start failing — which is the signal that you missed a host or service.

### 6. Record the rotation

Append a row to the drill log in `docs/operations/runbooks/key-rotation-drill-log.md` (or the legacy `docs/runbooks/key_rotation_drill_log.md` if that's the active log on your deploy) with `Key Type = ANTHROPIC_API_KEY`.

## Emergency rotation (key leaked)

If the key is in a public commit, CI log, or shared with a third party:

1. Skip the overlap window. Revoke the leaked key in the Anthropic console **first**.
2. Push the new key into SSM (step 2).
3. Restart the gateway (step 3) — accept the brief LLM router 5xx window for in-flight inference.
4. Audit `acp_audit_logs WHERE action='llm_inference' AND created_at >= '<leak_window_start>'` for unexpected upstream call volume.
5. File an incident report. Customer notification depends on whether tenant data left the inference path during the leak window.

## Rollback

If the new key turns out to be wrong (e.g., scoped to the wrong org):

1. Revert SSM to the previous key value. SSM keeps a 100-version history per parameter; the previous version is one `aws ssm get-parameter-history` away.
2. Restart the gateway.
3. Do NOT revoke the previous key until rollback is confirmed working.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Gateway logs `invalid_api_key` after restart | SSM read failed or stale env | Confirm the container's ENV file references SSM; re-restart |
| Inference latency spikes after rotation | Anthropic rate-limit bucket reset on new key | Wait 5 min for the bucket to settle; if persistent, raise rate-limit with Anthropic |
| Half the inference 200s, half 401 | Only one ASG host restarted | Restart the other host |
| Wizard-registered agents fail registration | `services/registry/wizard.py` reads `ANTHROPIC_API_KEY` at request time; gateway has been restarted but registry has not | Restart `acp_registry` |
| Drill log entry missing | Operator forgot | Append retroactively with actual rotation timestamp |

## Related code

- `services/gateway/llm_router.py:628` — the gateway's Anthropic client construction
- `services/registry/wizard.py:394` — wizard reads the key for agent provisioning
- `sdk/common/config.py:264` — `ANTHROPIC_API_KEY` settings field

## Next

- [Key Rotation](../key-rotation.md) — signing keys (transparency, mesh, JWT, INTERNAL_SECRET)
- [Secret Management](../../security/secret-management.md) — full secret inventory
- [Observability](../observability.md) — alert routing and dashboards
