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

### 4. Record the rotation
Log the date in `docs/runbooks/key_rotation_drill_log.md`.

## Rollback
If any service returns 401/403 after rotation, redeploy with the previous secret from the secrets store. The old transparency key is retained in `transparency_historical_keys` — no rollback needed there.

## See also
- `scripts/maintenance/rotate_transparency_key.py`
- `docs/runbooks/key_rotation_drill_log.md`
