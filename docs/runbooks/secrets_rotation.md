# Aegis — Secrets Rotation Runbook

## Inventory of every secret + where it lives

| Secret | Location | Reader | Rotation cadence | Method |
|--------|----------|--------|------------------|--------|
| `DB master password` | Secrets Manager `aegis-prod-db-master-password` | RDS + app via SSM-read at boot | 90 d | Terraform `taint random_password.db_master` |
| `JWT signing key` (HS256) | Secrets Manager `aegis-prod-jwt-signing-key` | Gateway, identity | 90 d | Terraform taint + restart |
| `Internal service secret` | Secrets Manager `aegis-prod-internal-secret` | Every service (mesh auth) | 90 d | Coordinated rolling restart |
| `Mesh JWT secret` | Secrets Manager `aegis-prod-mesh-jwt-secret` | Service-to-service | 90 d | Same as internal_secret |
| `Redis AUTH token` | Secrets Manager `aegis-prod-redis-auth-token` | All services | 180 d | ElastiCache rotate-auth-token + app restart |
| `Stripe webhook secret` | Secrets Manager `aegis-prod-stripe-webhook-secret` | Gateway (`/billing/stripe/webhook`) | When Stripe rotates | Operator paste + put-secret-value |
| `Groq API key` | Secrets Manager `aegis-prod-groq-api-key` | Gateway demo path | On suspected leak | Operator paste |
| `Anthropic API key` | SSM `/aegis-prodha/anthropic/upstream-key` | Gateway `/v1/messages` proxy | 90 d or per-tenant (see §3) | `aws ssm put-parameter --overwrite` |
| `Clerk secret key` | SSM `/aegis-prodha/clerk/secret-key` | Gateway, identity | On Clerk dashboard rotation | Operator paste |
| `Clerk webhook secret` | SSM `/aegis-prodha/clerk/webhook-secret` | Identity `/webhooks/clerk` | On Clerk dashboard rotation | Operator paste |
| `Receipt signing key` (ed25519) | SSM `/aegis-prodha/receipt-signing-key` | Audit, transparency root signer | **Never** (rotation breaks chain — see §4) | Special procedure |

## 1 — Standard 90-day rotation (auto-generated secrets)

Cron-driven via GitHub Actions `.github/workflows/rotate_secrets.yml` (run on 1st of every 3rd month):

```bash
# Pick the secret to rotate
SECRET=jwt_signing   # or db_master, internal_secret, mesh_jwt_secret

cd infra/terraform
terraform taint module.secrets.random_password.${SECRET}
terraform apply -auto-approve

# Roll the app tier so workers pick up the new value
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name aegis-prod-asg \
  --preferences MinHealthyPercentage=100,InstanceWarmup=300
```

`MinHealthyPercentage=100` keeps both targets healthy during the rotation. JWTs issued before rotation will fail on the next request — Clerk sessions auto-refresh, legacy HS256 sessions force re-login.

## 2 — Operator-supplied secret rotation (Clerk / Stripe)

1. Generate new key in vendor dashboard.
2. Put into SSM:
```bash
aws ssm put-parameter \
  --region ap-south-1 \
  --name /aegis-prodha/clerk/webhook-secret \
  --value "$NEW_SECRET" \
  --type SecureString --overwrite
```
3. Rolling restart (no instance refresh needed if you just want the .env regenerated on boot):
```bash
for i in $(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names aegis-prod-asg \
  --query 'AutoScalingGroups[0].Instances[].InstanceId' --output text); do
  aws ssm send-command --instance-ids "$i" \
    --document-name AWS-RunShellScript \
    --parameters 'commands=["KEY=$(aws ssm get-parameter --region ap-south-1 --name /aegis-prodha/clerk/webhook-secret --with-decryption --query Parameter.Value --output text)","sed -i \"s|^CLERK_WEBHOOK_SECRET=.*|CLERK_WEBHOOK_SECRET=$KEY|\" /opt/aegis/infra/.env","docker restart acp_gateway acp_identity"]'
  sleep 90   # rolling
done
```
4. Smoke test:
```bash
curl -sS https://aegisagent.in/health
curl -sS -X POST https://aegisagent.in/webhooks/clerk -H "Svix-Signature: badsig" -H 'content-type: application/json' -d '{}'
# Expect: 401/403 with new signature error, not "secret not configured"
```

## 3 — Per-tenant LLM key (Anthropic)

The default `/v1/messages` proxy uses a single shared Anthropic key. For enterprise customers we move to per-tenant keys:

1. Per-tenant SSM parameter:
```
/aegis-prodha/anthropic/upstream-key-by-tenant/<tenant_uuid>
```

2. Gateway proxy `services/gateway/routers/messages.py` already supports the `tenant_id`-keyed lookup via `_get_upstream_key(tenant_id)` (falls back to the default if not set).

3. Operator workflow when customer signs commercial agreement:
```bash
aws ssm put-parameter \
  --name /aegis-prodha/anthropic/upstream-key-by-tenant/<tenant_uuid> \
  --value "$CUSTOMER_KEY" \
  --type SecureString
```

4. Tenant's cumulative inference USD is capped via `acp_identity.tenants.daily_inference_cost_cap_usd`; over-cap requests return 402 to the SDK.

## 4 — Receipt signing key — **DO NOT ROTATE CASUALLY**

The ed25519 key at SSM `/aegis-prodha/receipt-signing-key` is the root of the customer-verifiable transparency Merkle chain. Rotating it produces a chain discontinuity — every customer who archived a prior root.json sees a "new key id" event and **must explicitly trust** the new key.

**Only rotate when:**
- The key is suspected leaked.
- Cryptographic best-practice requires it (we plan one rotation per 3 years; ed25519 currently has no known breaks).

**Procedure (`scripts/maintenance/rotate_transparency_key.py`):**
1. Generate new ed25519 keypair.
2. Promote current key to `transparency_historical_keys` table (so old receipts still verify).
3. Publish new public key to `s3://aegis-public-roots-628478946931/keys/public-<new_kid>.pem`.
4. Publish a "rotation marker" signed by **both** old and new keys so customers can establish continuity.
5. Update SSM to the new private key.
6. Restart audit service to pick up the new key.

**Customer impact:** any prospect who has saved a root.json to disk for compliance evidence needs to confirm the rotation event manually. Their existing root remains verifiable against `keys/public-<old_kid>.pem`.

## 5 — Emergency revocation (suspected key leak)

| Step | Time |
|------|------|
| Mint new key (vendor or terraform taint) | 1 min |
| `put-secret-value` / `put-parameter --overwrite` | 1 min |
| Rolling restart both targets (parallel) | 6 min |
| Revoke prior key at vendor (Anthropic / Stripe / Clerk console) | 1 min |
| **Total RTO** | **< 10 min** |

During the 6-minute restart window: in-flight tokens MAY still validate. Worst case = a leaked HS256 JWT could authenticate for up to the JWT's `exp` (max 15 min). For a real incident, also publish to the Redis revocation channel:
```bash
docker exec acp_gateway python -c "
import os, redis
r = redis.from_url(os.environ['REDIS_URL'], ssl_cert_reqs=None)
r.set('acp:token:revoked:<token_hash>', '1', ex=3600)
r.publish('acp:token:revocations', '<token_hash>')
"
```

## Audit trail

Every put-parameter / put-secret-value call is logged to CloudTrail with the operator identity. CloudTrail logs ship to `s3://aegis-prod-cloudtrail-…/AWSLogs/…`. Quarterly compliance review reads this log to verify rotation cadence is met.
