# Key Rotation

*Rotate signing keys and inter-service secrets without breaking the audit chain. The historical-keys mechanism keeps old receipts verifiable forever.*

## What rotates and when

| Key | Maximum interval | Drill cadence | Owner |
|---|---|---|---|
| `RECEIPT_SIGNING_PRIVATE_KEY` (ed25519) | 90 days | 30 days | Audit |
| `ROOT_SIGNING_PRIVATE_KEY` (ed25519) | 90 days | 30 days | Audit |
| `INTERNAL_SECRET` | 30 days | 14 days | Platform |
| `JWT_SECRET_KEY` | 7 days | 7 days (automated) | Identity |

Automated rotation counts as a drill only when the acceptance criteria below are verified by a human operator.

## The non-negotiable rule

**The old key's fingerprint must be promoted to `transparency_historical_keys` BEFORE any row is written with the new key.**

A row signed by key K verifies against either the current key or any row in `transparency_historical_keys` with fingerprint K. Skipping the promote step invalidates every receipt issued before the rotation.

The automated script `scripts/maintenance/rotate_transparency_key.py` enforces the order. Manual rotation must follow the same sequence.

## Rotation steps

### Receipt and root signing keys (ed25519)

Sprint 1.3 introduced a `SigningKeyProvider` abstraction. The recommended
production path is **SSM Parameter Store** with a `SecureString` parameter:
SSM transparently encrypts under KMS, CloudTrail records every access, and
rotation is one `ssm:PutParameter` call — no application restart required.

#### Sprint 1.3 — SSM Parameter Store path

```bash
# 1. Generate the new ed25519 keypair offline.
python3 - <<'PY' > /tmp/new-receipt-key.pem
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
priv = ed25519.Ed25519PrivateKey.generate()
print(priv.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode())
PY

# 2. Promote the current key fingerprint to transparency_historical_keys
#    BEFORE flipping the SSM parameter — old receipts must remain verifiable.
.venv/bin/python scripts/maintenance/rotate_transparency_key.py

# 3. Push the new key into SSM (overwrites the existing version).
aws ssm put-parameter \
  --region ap-south-1 \
  --name /aegis-audit/receipt-signing-key \
  --type SecureString \
  --value "file:///tmp/new-receipt-key.pem" \
  --overwrite

# 4. Restart the audit service so the SigningKeyProvider re-reads SSM.
#    On the prod-ha reference deployment, target both ASG members:
INSTANCE_IDS=$(aws autoscaling describe-auto-scaling-groups \
  --region ap-south-1 \
  --auto-scaling-group-names acp-prodha-asg \
  | jq -r '.AutoScalingGroups[0].Instances[].InstanceId' | paste -sd ' ' -)

aws ssm send-command \
  --region ap-south-1 \
  --instance-ids $INSTANCE_IDS \
  --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker restart acp_audit"]'

# 5. Verify the new key is active and historical receipts still validate.
curl -sS https://ha.aegisagent.in/transparency/keys \
  -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" | jq
.venv/bin/acp verify-root --date 2026-05-01

# 6. Wipe the on-disk copy.
shred -u /tmp/new-receipt-key.pem
```

The IAM role on the audit container needs `ssm:GetParameter` on the
parameter ARN and `kms:Decrypt` on the CMK encrypting the SecureString
(typically `alias/aws/ssm` or a customer-managed CMK).

#### Legacy disk-file path (dev only)

```bash
# 1. Generate the new key offline
age-keygen -o /tmp/new-receipt-key.txt

# 2. Promote the current key to historical
.venv/bin/python scripts/maintenance/rotate_transparency_key.py

# 3. Inject the new key as the deploy's env var (do NOT commit to disk)
export RECEIPT_SIGNING_PRIVATE_KEY="$(cat /tmp/new-receipt-key.txt | base64 -w0)"

# 4. Deploy the audit service with the new env
# See operations/deployment.md

# 5. Verify the new key is active
curl -sS https://ha.aegisagent.in/transparency/keys -H "Authorization: Bearer $TOKEN" -H "X-Tenant-ID: $TENANT" | jq

# 6. Verify old receipts still validate
.venv/bin/acp verify-root --date 2026-05-01
```

### Per-service mesh keys (Sprint 1.4)

Each service owns one ES256 (ECDSA P-256) private key for mesh-JWT signing.
Rotate by generating a new keypair, publishing the new public key to the
trust registry FIRST (so verifiers accept both), then flipping the signer
to the new private key, then removing the old public key.

```bash
# 1. Generate the new keypair offline for one service (e.g. gateway).
openssl ecparam -name prime256v1 -genkey -noout -out /tmp/gateway-mesh-new.pem
openssl ec -in /tmp/gateway-mesh-new.pem -pubout -out /tmp/gateway-mesh-new.pub.pem

# 2. Update the trust registry with BOTH the old and new public keys for the
#    duration of the cutover. Every verifying service needs the update.
#    (Push to SSM Parameter Store the same way as receipt keys.)
NEW_PUB_B64=$(base64 -i /tmp/gateway-mesh-new.pub.pem)
aws ssm put-parameter --region ap-south-1 \
  --name /aegis-mesh/trusted-keys \
  --type SecureString \
  --value "$(jq --arg p \"$NEW_PUB_B64\" '. + {\"gateway-v2\": $p}' /tmp/current-registry.json)" \
  --overwrite

# 3. Once every verifier has reloaded, push the new private key to the
#    gateway and switch ACP_MESH_SERVICE_NAME=gateway-v2.
aws ssm put-parameter --region ap-south-1 \
  --name /aegis-mesh/gateway-private-key \
  --type SecureString \
  --value "$(base64 -i /tmp/gateway-mesh-new.pem)" --overwrite

# 4. After the rotation window closes (mesh JWT TTL is 5 min by default),
#    remove the old public key from the trust registry.
```

See [Mesh Authentication](../security/mesh-auth.md) for the full design and
the migration order from the legacy HS256 / `INTERNAL_SECRET` lane.

### `INTERNAL_SECRET`

```bash
# 1. Generate a new 32-byte secret
NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# 2. Set INTERNAL_SECRET_PREVIOUS to the current value, INTERNAL_SECRET to the new
#    (overlap window: both verify during the cutover)
#    Update every service's env via the deploy SSM script

# 3. Deploy gateway, audit, decision, policy, registry, identity, behavior, autonomy,
#    flight_recorder, identity_graph, forensics, api, usage, insight, learning, groq_worker
docker compose -f /home/ubuntu/aegis/infra/docker-compose.yml up -d --force-recreate \
    gateway audit decision policy registry identity behavior autonomy \
    flight_recorder identity_graph forensics api usage insight groq_worker

# 4. Verify all services healthy
curl -sS https://ha.aegisagent.in/system/health -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" | jq '.data.services'

# 5. Drop INTERNAL_SECRET_PREVIOUS once all services confirmed healthy
#    (next deploy without the previous var; this completes the rotation)
```

### `JWT_SECRET_KEY`

Automated weekly rotation. The script:

1. Generates a new HS256 secret.
2. Sets the new value in the identity service's env.
3. Sets the new value in the gateway's env.
4. Deploys both services.
5. Existing tokens 401 until users re-login.

Plan automated rotations during low-traffic windows. The C-5 mitigation (`services/gateway/auth.py:12`) cross-checks against an Identity-side `active_key` Redis entry so a stolen `JWT_SECRET_KEY` alone cannot mint indefinite tokens.

## Acceptance criteria for a successful drill

A rotation drill is PASSED when **all** of the following hold:

1. **New key active.** `GET /transparency/keys` returns the new key fingerprint as the primary.
2. **Historical key retained.** The old key fingerprint appears in the `historical_keys` array, not as primary.
3. **Old receipts still verify.** `acp verify-root` against a root signed with the old key returns `valid: true`.
4. **Chain unbroken.** `acp verify-chain` returns `violations=0` immediately after rotation.
5. **All services healthy.** `GET /system/health` shows all downstream services as `healthy`.
6. **Inter-service auth intact.** At least one `/execute` call succeeds end-to-end within 60 seconds of rotation completing.

A drill is FAILED if any step does not hold. Record the failure mode and open a P1 incident.

## Drill log

After each rotation, append a row to `docs/runbooks/key_rotation_drill_log.md`:

| Date | Operator | Key Type | Duration | Notes |
|---|---|---|---|---|
| 2026-05-17 | system | transparency_root | 4m | Initial rotation test — chain re-verified, all receipts valid post-rotation |

The log is the SOC 2 audit trail for key management. Auditors review it during compliance assessments.

## Verification commands

```bash
# Current key + historical keys
curl -sS https://ha.aegisagent.in/transparency/keys \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" | jq '{ primary: .data.primary_fingerprint, historical: .data.historical_keys | length }'

# Verify a receipt from before the rotation
RECEIPT_ID=<old audit row id>
curl -sS "https://ha.aegisagent.in/audit/logs/$RECEIPT_ID/receipt" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" | jq '.data.signature_valid'

# Verify the daily root from before the rotation
DATE=<yyyy-mm-dd>
curl -sS "https://ha.aegisagent.in/transparency/roots/$DATE" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-ID: $TENANT" | jq '.data.signature_valid'
```

All three should return `true`.

## Rollback

If rotation produces 401s or chain-verification failures:

1. Redeploy with the previous secret from the secrets store.
2. The previous key in `transparency_historical_keys` continues to verify the post-rotation rows (because they were signed with the previous key during the overlap).
3. Run `acp verify-chain` to confirm.
4. File an incident.

The historical-keys mechanism makes rollback safe — no receipt verification is lost.

## Common failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `acp verify-root` fails on rows from before the rotation | Old key not promoted to historical before deploy | Re-run the promote script; the historical table is append-only and idempotent |
| Every inter-service call 401s after rotation | `INTERNAL_SECRET` mismatch across services | Confirm every service was redeployed with the new env |
| Some services 401, others 200 | Partial rollout; some restarts didn't pick up new env | Force-recreate the remaining services |
| `GET /transparency/keys` does not show the new key | The audit service's env was not updated | Confirm SSM parameter contains the new key |
| Drill log row missing | Operator forgot to record | Append retroactively with the actual rotation timestamp |

## What this rotation does NOT cover

- **Hardware key management.** Aegis assumes the operator's secret store (Vault, AWS Secrets Manager, hardware key) is sound. Aegis does not provide a hardware-key abstraction.
- **Customer-side key archival.** Customers archiving daily roots are responsible for keeping their archive secure. Aegis cannot help recover a lost customer archive.
- **Mid-day rotation.** Rotation completes within minutes but the platform is briefly in a mixed state. Plan rotations during low-traffic windows.

## Next

- [Audit service](../services/audit.md) — owns the key infrastructure
- [Cryptographic Audit Chain](../security/crypto-audit-chain.md) — why the historical-keys mechanism matters
- [Secret Management](../security/secret-management.md) — the full secret inventory
- [Audit Chain Violation runbook](runbooks/audit-chain-violation.md) — what to do when chain verify fails post-rotation
