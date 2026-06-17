# Anthropic Upstream API-Key Rotation

The `/v1/messages` Aegis proxy forwards to `api.anthropic.com` using one
corporate Anthropic API key, stored in SSM at
`/aegis-prodha/anthropic/upstream-key` and consumed by the gateway as
the `UPSTREAM_ANTHROPIC_KEY` env var. This runbook is the procedure for
rotating that key.

## When to rotate

- Routine: every 90 days.
- Immediately: any time the raw key value has appeared somewhere it
  shouldn't (chat logs, support tickets, shared docs).
- After offboarding any engineer with access to SSM SecureString reads
  in `ap-south-1`.

## Pre-requisites

- AWS CLI configured with the prod-ha admin profile
  (`aws sts get-caller-identity` should return the prod account).
- One operational `acp_emp_…` virtual key for the post-rotation smoke
  test. Mint one via the Team page if you don't have one handy.

## Procedure

### 1. Revoke the old key

Console-only — there is no Anthropic Backend API to do this
programmatically.

1. Sign in at https://console.anthropic.com/settings/keys.
2. Identify the active key (the dashboard prefix should match the
   first eight characters returned by
   `aws ssm get-parameter --name /aegis-prodha/anthropic/upstream-key --with-decryption --query Parameter.Value --output text | head -c 8`).
3. Click "Revoke".

The Aegis proxy will start returning 401 from `/v1/messages` the next
time it forwards. Treat the rotation as time-critical from here.

### 2. Mint the new key

1. In the same dashboard, "Create Key".
2. Copy the new value (starts with `sk-ant-`). The dashboard will not
   show it again.

### 3. Push to SSM and restart

One command:

```bash
python3 scripts/ops/rotate_anthropic_key.py \
    --new-key 'sk-ant-api03-…' \
    --restart \
    --smoke \
    --smoke-key 'acp_emp_…'
```

What the script does:

1. Validates the new value looks like an Anthropic key
   (`^sk-ant-[A-Za-z0-9_-]{20,}$`).
2. `aws ssm put-parameter --type SecureString --overwrite` against
   `/aegis-prodha/anthropic/upstream-key`.
3. SSM Run-Command on both prod-ha instances
   (`i-076882fbef70af91f` + `i-05a13cd061d30849d`):
   re-reads the SSM value, appends it to
   `/opt/aegis/infra/.env`, restarts `acp_gateway`.
4. Calls `POST https://ha.aegisagent.in/v1/messages` with the supplied
   employee key and expects HTTP 200.

If you only want the SSM update without a restart (e.g. you'll do the
restart during a maintenance window), drop `--restart` and `--smoke`.

### 4. Verify

If the script's smoke test passes you're done. If you want extra
assurance:

```bash
# Per-host gateway logs — look for llm_proxy_call rows.
aws ssm send-command --instance-ids i-076882fbef70af91f \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker logs --tail 50 acp_gateway 2>&1 | grep llm_proxy"]' \
  --region ap-south-1
```

### 5. Drill log

Append a row to `docs/runbooks/key_rotation_drill_log.md` with the
date, the operator, the old key's first eight characters, the new key's
first eight characters, and whether the smoke test passed.

## Failure modes

| Symptom | Diagnosis | Fix |
| --- | --- | --- |
| `--smoke` returns HTTP 401 | Gateway still has the old env var | Re-run with `--restart` |
| `--smoke` returns HTTP 502 from Aegis | `acp_gateway` not back up yet | Wait 10s, run smoke again |
| `POST /v1/messages` returns 401 from upstream Anthropic | New key is wrong or not active | Verify in the console |
| SSM put-parameter fails | IAM lacks `ssm:PutParameter` on the path | Re-auth with the prod-ha admin profile |

## Multi-tenant follow-on

This runbook assumes one corporate Anthropic key shared across all
tenants. Sprint 18+ will introduce a per-tenant encrypted column on
`tenants` so each customer can BYO key; once that lands, the rotation
procedure becomes a per-tenant operation in the Settings → Billing tab
and this runbook applies only to the system-default key.
