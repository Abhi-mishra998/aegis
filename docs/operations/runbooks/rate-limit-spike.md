# Runbook: Rate Limit Spike

## Alert

`RateLimitSpike` — fires when per-IP authentication failure counters or per-tenant rate-limit reject counts exceed normal levels.

## Severity

**P2.** A spike is usually not an emergency — the rate limiter is doing its job. The alert exists so an operator can confirm "is this a real attack or an integration bug."

## Triage in 5 minutes

### 1. Identify the source

```bash
# Top IPs by auth failure count in the last 5 minutes
redis-cli --scan --pattern 'acp:auth_fail:*' | while read k; do
  v=$(redis-cli get "$k")
  echo "$v $k"
done | sort -rn | head -10

# Top tenants by 429 count
curl -sS -G "http://localhost:9090/api/v1/query" \
  --data-urlencode 'query=topk(5, sum by (tenant_id) (rate(acp_gateway_request_total{status="429"}[5m])))' | jq
```

### 2. Categorize

| Category | Pattern | Likely cause |
|---|---|---|
| One IP, many auth failures | One source, many `acp:auth_fail` entries | Credential stuffing or brute force |
| One tenant, many 429s, mix of paths | Tenant exceeded RPS cap | Legitimate burst or misconfigured client |
| One tenant, many 429s, all `/execute` | Tenant exceeded per-agent cost cap | Inference spend cap hit |
| Many tenants, many 429s, all paths | Platform-wide overload | Capacity issue |
| Many IPs, many auth failures | Distributed credential stuffing | Coordinated attack |

## Per-category response

### Single IP credential stuffing

The platform's per-IP fail counter (`acp:auth_fail:{ip}` with 5-minute TTL) already throttles the source. To add a hard block:

```bash
# Add the IP to the Redis-backed deny list
redis-cli set "acp:ip_block:203.0.113.42" "credential_stuffing_2026_05_29" EX 86400

# The auth path in services/gateway/_mw_auth.py reads this and returns 403
# without invoking the rest of the pipeline.
```

For a more durable block, add the IP to the ALB security group's deny list at the AWS console. The Redis block survives until TTL expiry; the ALB block survives indefinitely.

### Tenant exceeded RPS cap

This is the rate limiter working as designed. The tenant should either:

- Reduce the rate from the SDK side (the SDK respects `Retry-After`).
- Request a higher cap via Settings → Quota Management → Budget Request.

Operator action: confirm with the tenant's contact whether the burst is expected. If yes, raise the cap; if no, investigate why the integration is sending more traffic than authorized.

### Tenant exceeded per-agent cost cap

The agent's daily USD spend (`acp:agent_cost_today:{agent_id}:{YYYYMMDD}`) crossed `acp:agent_cost_cap:{agent_id}`. Subsequent `/execute` calls return 429 with `limit_type: "agent_cost"`.

```bash
# Inspect current spend and cap
redis-cli get "acp:agent_cost_today:${AGENT_ID}:$(date -u +%Y%m%d)"
redis-cli get "acp:agent_cost_cap:${AGENT_ID}"
```

To raise the cap:

```bash
redis-cli set "acp:agent_cost_cap:${AGENT_ID}" "100.00"
```

The new cap takes effect immediately for the next request.

For a permanent raise, set the value via the API:

```bash
curl -sS -X PATCH https://ha.aegisagent.in/agents/$AGENT_ID \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" \
  -H "Content-Type: application/json" \
  -d '{"metadata":{"daily_cost_cap_usd":100.00}}'
```

### Platform-wide overload

If many tenants are hitting 429s simultaneously, the platform is at capacity:

1. Check Settings → System Health for any degraded services.
2. Check Grafana → Queues dashboard for stream backlog.
3. Check `acp_gateway_inflight_requests` — if approaching the configured limit, the gateway is shedding.

Mitigation options, in order:

- **Scale gateway workers.** Increase `UVICORN_WORKERS` in the deploy env and redeploy the gateway.
- **Scale EC2.** If both EC2s are at CPU 100%, replace with larger instances.
- **Add a third EC2.** ALB target group can hold more than two hosts.

### Distributed credential stuffing

Multiple IPs hitting the auth endpoint. Treat as a coordinated attack:

1. Engage the kill switch for the affected tenants if the attack is succeeding.
2. Enable SSO if not already, and disable password login for the duration.
3. Rotate `JWT_SECRET_KEY` to invalidate any tokens the attacker may have stolen.
4. Add the source IP range to the ALB deny list.

## Post-incident

1. File an incident report with the source IPs, the response taken, and the duration.
2. Tune the rate limit if the attack revealed a gap (e.g., per-IP cap was too lenient).
3. If the attack hit a specific tenant, notify the contact.
4. Add a regression test that triggers the same pattern at lower volume.

## Common confusion

- **429 ≠ attack.** Legitimate tenants hit 429 when they burst. The 429 with `Retry-After` is the contract; the SDK retries appropriately.
- **`acp:auth_fail` ≠ active attack.** A fresh deploy that briefly invalidated tokens will produce a flurry of fails as clients reconnect. Time-correlate with deploy events before assuming attack.
- **Kill switch ≠ rate limit response.** The kill switch is for "we know something is wrong"; the rate limit is "this client is sending too fast." Use the right lever.

## Related code

- `sdk/common/ratelimit.py::TOKEN_BUCKET_SCRIPT` — the Redis Lua script
- `services/gateway/_mw_rate_limit.py::_RateLimitMixin` — the gateway integration
- `services/gateway/_mw_auth.py:150-200` — the per-IP fail counter
- `services/gateway/middleware.py` — the cost-cap check

## Next

- [Gateway service](../../services/gateway.md) — the rate-limit implementation
- [Quota Management UI](../../ui/settings/quota-management.md) — operator surface for tenant caps
- [Kill Switch runbook](kill-switch-engaged.md) — the bigger lever
