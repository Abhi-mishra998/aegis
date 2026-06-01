# Key Rotation Runbook

## Scope
Rotate ACP signing keys (transparency root key + INTERNAL_SECRET inter-service auth key) without audit-chain downtime.

## Prerequisites
- Admin access to the ACP deployment
- `scripts/maintenance/rotate_transparency_key.py` available
- Vault / secrets store write access (or docker env for local)

## Steps

### 1. Rotate Transparency Signing Key
```bash
# Generate new key, promote current to historical_keys
.venv/bin/python scripts/maintenance/rotate_transparency_key.py

# Verify old receipts still validate
.venv/bin/acp verify-root
```

### 2. Rotate INTERNAL_SECRET
```bash
# Generate a new 32-byte secret
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Update all services (gateway, audit, decision, etc.) via your secrets store
# For Docker: update docker-compose.yml environment block and restart services
docker compose -f infra/docker-compose.yml up -d gateway audit decision policy usage billing
```

### 3. Verify
```bash
# All inter-service calls should return 200
curl -s http://localhost:8000/system/health | jq '.data.services'

# Chain integrity must still hold
.venv/bin/acp verify-chain
```

### 3a. Rotate JWT_SECRET_KEY (sprint-7.7 — graceful, no forced re-login)

**The gotcha:** changing `JWT_SECRET_KEY` instantly invalidates every
issued token (signature mismatch). The graceful rotation below avoids
the customer-visible "everyone is suddenly logged out" outage.

```bash
# 1. Generate the new secret.
NEW_JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(64))")

# 2. Add it to `infra/.env` as a SECONDARY secret. The gateway accepts
#    BOTH the primary and secondary during the rotation window.
#    Edit infra/.env to set:
#       JWT_SECRET_KEY=<existing primary>
#       JWT_SECRET_KEY_NEXT=<NEW_JWT_SECRET>
#
#    NOTE: graceful rotation requires gateway support for the
#    JWT_SECRET_KEY_NEXT env var — this is a sprint-7.7 follow-up if not
#    yet shipped. Until then, accept the forced re-login during rotation
#    and do it during a low-traffic window (02:00–04:00 UTC).
aws s3 cp infra/.env s3://acp-backups-prod-am/config/.env

# 3. Roll the gateway (and identity, which signs new tokens) to pick up
#    the new env. Issued tokens with the OLD secret still validate.
docker compose -f infra/docker-compose.yml up -d gateway identity

# 4. Wait 15 minutes (the default JWT TTL — JWT_EXPIRY_MINUTES=15 in
#    sdk/common/config.py:58). After this window every active token has
#    been re-issued under the new secret.
sleep 900

# 5. Promote the secondary to primary; delete the old.
#    Edit infra/.env so JWT_SECRET_KEY=<new> and JWT_SECRET_KEY_NEXT="".
aws s3 cp infra/.env s3://acp-backups-prod-am/config/.env
docker compose -f infra/docker-compose.yml up -d gateway identity

# 6. Verify.
./scripts/ops/smoke_test.sh   # smoke-test the deploy
.venv/bin/acp verify-chain    # chain still valid (it's HMAC-independent)
```

### 4. Record the rotation
Log the date in `docs/runbooks/key_rotation_drill_log.md`.

## Rollback
If any service returns 401/403 after rotation, redeploy with the previous secret from the secrets store. The old transparency key is retained in `transparency_historical_keys` — no rollback needed there.

For `JWT_SECRET_KEY` rotation: if the new key was promoted prematurely
(step 5 before step 4 completed), restore the previous `infra/.env` from
S3 — every JWT issued before the rotation continues to validate as long
as the previous secret is the primary.

## Published key fingerprints

After every rotation, append the new public key's fingerprint here so
external verifiers can confirm they're checking signatures against the
right key:

```
2026-05-15  ed25519 fingerprint: 7a8b9c0d1e2f...  (initial production key)
```

When you rotate, append a new line — never delete an old entry. Existing
receipts may still be verified against the historical key.

## See also
- `scripts/maintenance/rotate_transparency_key.py`
- `docs/runbooks/key_rotation_drill_log.md`
- `scripts/ops/smoke_test.sh` — run after every rotation
