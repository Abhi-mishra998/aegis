# Key Rotation Drill Log

Each entry records a completed key rotation drill. Operators must complete a drill within 30 days of the previous one.

| Date | Operator | Key Type | Duration | Notes |
|------|----------|----------|----------|-------|
| 2026-05-17 | system | transparency_root | 4m | Initial rotation test — chain re-verified, all receipts valid post-rotation |

## How to add an entry
After completing the steps in `key_rotation.md`, append a row to the table above with:
- ISO date (YYYY-MM-DD)
- Operator username or "system" for automated rotation
- Key type rotated
- Total time taken
- Any anomalies or notes

## Acceptance criteria for each drill

A drill is considered PASSED when all of the following are confirmed:

1. **New key active** — `GET /transparency/keys` returns the new key fingerprint as the primary key.
2. **Historical key retained** — the old key fingerprint appears in the `historical_keys` array, not as primary.
3. **Old receipts still verify** — re-running `acp verify-root` against a root signed with the old key returns `valid: true`.
4. **Chain unbroken** — `acp verify-chain` returns `violations=0` immediately after rotation.
5. **All services healthy** — `GET /system/health` shows all downstream services as `healthy`.
6. **Inter-service auth intact** — at least one `/execute` call succeeds end-to-end within 60 seconds of rotation completing.

A drill is FAILED if any step above does not hold. Record the failure mode and open a P1 incident.

## Rotation frequency policy

| Key Type | Maximum rotation interval | Mandatory drill interval |
|----------|--------------------------|--------------------------|
| transparency_root | 90 days | 30 days |
| INTERNAL_SECRET | 30 days | 14 days |
| JWT signing key | 7 days | 7 days (automated) |

Automated rotation (via `scripts/maintenance/rotate_transparency_key.py`) counts as a drill only when the acceptance criteria above are verified by a human operator.

## References
- `docs/runbooks/key_rotation.md` — step-by-step procedure
- `scripts/maintenance/rotate_transparency_key.py` — automated rotation script
- `services/audit/transparency_signer.py` — signing implementation
- `sdk/common/merkle.py` — Merkle tree and root chaining logic
