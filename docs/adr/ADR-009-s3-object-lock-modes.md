# ADR-009: S3 Object Lock — GOVERNANCE for backups, COMPLIANCE for CloudTrail

* Status: Accepted
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: storage, compliance, immutability, soc2, recovery

## Context

Aegis owns three classes of S3 bucket whose contents must outlive any
single insider attack:

- **`aegis-prod-backups-…-v2`** — RDS snapshots (age-encrypted), audit-
  pipeline backups, deploy bundle archives. Loss of this bucket means
  loss of every recovery option short of going back to the last public
  transparency root.
- **`aegis-prod-cloudtrail-…-v2`** — AWS account-level CloudTrail
  events: every IAM action, every KMS Encrypt/Decrypt, every S3 PUT,
  every console login. Loss of this bucket means the forensic trail of
  an attacker disappears.
- **`aegis-public-roots-…`** — daily ed25519-signed Merkle roots (per
  ADR-001 + ADR-005), publicly readable. Loss means we can't prove the
  audit chain hasn't been silently rewritten by us.

AWS S3 Object Lock prevents object deletion or overwrite for a
specified retention period. It has two modes:

- **GOVERNANCE** — protects objects from deletion, BUT an IAM principal
  with the `s3:BypassGovernanceRetention` permission can clear the
  retention and delete. Useful when you want a tamper-evident default
  + an explicit, audited override path.
- **COMPLIANCE** — no IAM principal can shorten or remove the retention
  during the period, INCLUDING the root account. Once an object is
  Object-Locked in COMPLIANCE mode for N days, it physically cannot be
  deleted for N days.

The question this ADR closes is: **which mode for which bucket?**

## Decision

Per `infra/terraform/modules/s3/main.tf:112-119,181-186`:

| Bucket | Mode | Retention | Rationale |
|---|---|---|---|
| `aegis-prod-backups-…-v2` | **GOVERNANCE** | 35 days | The operator legitimately needs to be able to delete old recovery snapshots — at scale, snapshot storage compounds at $0.05/GB/month and a year-old snapshot from a deleted tenant is dead weight. GOVERNANCE means deletion requires a deliberate IAM grant (`s3:BypassGovernanceRetention`); accidental `aws s3 rm --recursive` from the operator's terminal fails. |
| `aegis-prod-cloudtrail-…-v2` | **COMPLIANCE** | 180 days | If an attacker steals root credentials, CloudTrail is the only record of what they did. COMPLIANCE mode means even root cannot wipe the trail during the 180-day window — exactly the property a forensic investigator needs. We accept the cost of "we can't shorten retention if our cloud bill blows up" because the data is the evidence of the incident. |
| `aegis-public-roots-…` | **(neither)** — versioning only | n/a | Public bucket, no IAM principals can write outside our account, and a malicious overwrite by us is detectable via the `prev_root_hash` chain. Object Lock on a versioned bucket would add storage cost without adding tamper-evidence beyond what the chain already provides. |

Migration of pre-existing (Object-Lock-disabled) buckets is handled by
`docs/runbooks/object_lock_migration.md` — AWS does not permit flipping
Object Lock on an existing bucket; the runbook walks the
copy-to-`-v2`-bucket + DNS/alias flip + decommission-old.

## Alternatives considered

1. **COMPLIANCE mode on all three buckets.** Maximum tamper-evidence.
   Rejected for backups because:
    - Snapshot retention exceeding a useful window starts costing
      real money at scale; we want the operator to be able to prune
      via an explicit, audited IAM grant.
    - Compliance-mode-by-default on backups would mean a one-character
      typo in a Terraform retention value locks 180+ days of snapshots
      we can't delete even if we re-do them correctly the next day.
   COMPLIANCE on CloudTrail is the right call because the operator
   legitimately should NEVER want to delete a CloudTrail event ahead
   of policy.
2. **GOVERNANCE mode on all three.** Easier operability. Rejected on
   CloudTrail because the bucket exists specifically to outlive a
   credential compromise — and any IAM principal with
   `s3:BypassGovernanceRetention` becomes the new single point of
   compromise. CloudTrail must not have one of those.
3. **No Object Lock; versioning + lifecycle policy only.** What the
   pre-EH-5 setup had. Rejected — `aws s3 rm --recursive` deletes
   versions too with the right flag; the lifecycle policy keeps them
   recoverable for 90 days but they ARE deleted from the listing.
   Doesn't survive an attacker with admin IAM.
4. **MFA Delete** on the buckets. Considered as an extra safeguard
   alongside GOVERNANCE. Rejected because MFA Delete blocks
   programmatic delete entirely — Terraform can't manage bucket
   state without operator interaction. Operationally noisy without
   adding much beyond GOVERNANCE.
5. **External backup vault** (AWS Backup, third-party SaaS). On
   roadmap. Rejected for THIS iteration because Object Lock + S3
   versioning is already the AWS-native answer the SOC 2 auditor
   wants; adding a vendor is overhead for marginal benefit at our
   scale.

## Consequences

* **Positive**
  - `aws s3 rm --recursive` from the operator's terminal accidentally
    deleting a backup bucket fails until they get a separate IAM
    grant for `s3:BypassGovernanceRetention` — exactly the friction
    we want.
  - CloudTrail tamper-evidence is COMPLIANCE-mode-strong — an
    attacker who steals root can't cover their tracks for 180 days.
  - The asymmetry (GOVERNANCE for ops-controlled buckets, COMPLIANCE
    for forensics-controlled buckets) is the principle a regulator
    will recognise as appropriate, vs the dogmatic "compliance on
    everything" stance that produces operational pain.
  - Per ADR-006, all three buckets are AES-256 encrypted at rest;
    Object Lock is the orthogonal "can it be deleted?" question.
* **Negative**
  - GOVERNANCE-mode default means an operator with the right IAM
    grant CAN still delete a backup. The defence is procedural
    (no one has the bypass grant standing; granted only for an
    explicit deletion runbook).
  - COMPLIANCE-mode CloudTrail means we are committed to ≥ 180 days
    of CloudTrail storage cost regardless of cost pressure. At our
    scale (~$10/mo for the CloudTrail bucket) this is trivial; at
    1000× scale this is a line item.
  - Migration from pre-existing buckets is a one-shot operation we
    have to do during a low-traffic window. The runbook handles it
    but each bucket migration is ~30 minutes of attention.
* **Reversibility**
  - **GOVERNANCE → COMPLIANCE upgrade is one-way** (you can tighten,
    not loosen).
  - **COMPLIANCE → GOVERNANCE is impossible** within the retention
    period. You have to wait out the existing retention before any
    relaxation takes effect on new objects.
  - **Object Lock disable** is impossible on an existing bucket
    once enabled. The only way out is the same migration runbook
    in reverse (create new bucket without Object Lock, copy data).

## Implementation references

* `infra/terraform/modules/s3/main.tf:112-119` — backups bucket
  `mode = "GOVERNANCE"` + 35-day retention
* `infra/terraform/modules/s3/main.tf:181-186` — cloudtrail bucket
  `mode = "COMPLIANCE"` + 180-day retention
* `infra/terraform/modules/s3/main.tf:170-174` — comment block
  explaining the asymmetry choice
* `docs/runbooks/object_lock_migration.md` — pre-existing bucket
  migration procedure
* OP-3 row in `testing.md` — confirms v2 buckets created with
  Object Lock from creation
* `docs/security/data_classification.md` — maps each bucket to its
  data class tier

## Verification

```bash
# 1. Confirm backups bucket has GOVERNANCE mode, 35-day retention.
aws s3api get-object-lock-configuration \
  --bucket aegis-prod-backups-628478946931-v2 \
  --query 'ObjectLockConfiguration.Rule.DefaultRetention'
# expect: {"Mode":"GOVERNANCE","Days":35}

# 2. Confirm cloudtrail bucket has COMPLIANCE mode, 180-day retention.
aws s3api get-object-lock-configuration \
  --bucket aegis-prod-cloudtrail-628478946931-v2 \
  --query 'ObjectLockConfiguration.Rule.DefaultRetention'
# expect: {"Mode":"COMPLIANCE","Days":180}

# 3. Confirm BypassGovernanceRetention is NOT granted to the EC2 role
#    (it should require a separate manual IAM update for any deletion).
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::628478946931:role/aegis-prod-ec2-role \
  --action-names s3:BypassGovernanceRetention \
  --resource-arns arn:aws:s3:::aegis-prod-backups-628478946931-v2/* \
  --query 'EvaluationResults[0].EvalDecision'
# expect: "implicitDeny"

# 4. Confirm public-roots bucket is versioned but NOT Object-Locked
#    (per the asymmetry decision).
aws s3api get-object-lock-configuration \
  --bucket aegis-public-roots-628478946931 2>&1 | head -2
# expect: error "Object Lock configuration does not exist" — by design.
```
