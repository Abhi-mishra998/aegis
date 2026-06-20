# ADR-006: Customer-managed KMS CMK per region for audit envelope

* Status: Accepted (current state); supersedes the aspirational per-tenant
  reference in ADR-001 §1.3
* Date: 2026-06-20
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: crypto, kms, audit, key-management, multi-tenant

## Context

Aegis's audit pipeline wraps each receipt's ed25519 signature in an
envelope encrypted at rest (in addition to RDS storage-layer AES-256
which is enabled by default). The envelope-encryption layer exists
because:

- Storage-layer encryption is invisible to the database role — a SQL
  query against `audit_logs` returns plaintext sig bytes from the
  application's perspective. A second envelope means a compromised
  read-only DB role still can't read the signature bytes without an
  AWS IAM grant.
- KMS Encrypt/Decrypt calls are logged to CloudTrail with the calling
  principal, request id, and the timestamp. Compared to "did this DB
  role read audit_logs?" (one log per query), this gives a much finer
  trail of "did this *application instance* envelope-open this
  receipt?".
- A `kms:DisableKey` call instantly stops any decryption — even by
  the application — without touching the DB or the application.
  That's a real kill-switch.

The question this ADR closes is: **is the CMK one-per-region or
one-per-tenant?**

ADR-001 §1.3 referenced "per-tenant KMS CMK" as a property of the
design. That was aspirational — the current implementation in
`infra/terraform/modules/audit_kms/main.tf:12-29` creates a **single
CMK per region** (`alias/aegis-audit-envelope`), shared across every
tenant in that region. ADR-001 is now corrected by reference to this
ADR.

## Decision

We will keep **one customer-managed KMS CMK per AWS region**, aliased
`alias/aegis-audit-envelope`, with:

- `enable_key_rotation = true` — AWS rotates the underlying key
  material annually; the alias and key id are stable across rotations
  so application code never re-pins.
- `deletion_window_in_days = 30` — max window. Rotation is the safer
  knob; deletion would brick every receipt envelope in the region.
- IAM policy that grants the EC2 instance role Encrypt / Decrypt /
  GenerateDataKey / DescribeKey on this key only.
- All KMS API calls land in CloudTrail at the AWS-account level.

When the EU instance comes up (Sprint EI-5, `eu-west-1`), it gets its
own CMK in `eu-west-1` — no cross-region key sharing, no cross-region
KMS calls.

**Per-tenant CMKs are on the roadmap** for any customer whose contract
requires "you must lose the ability to read MY audit envelope on 24
hours' notice." That requirement has not landed from a paying customer
yet; when it does, the migration is mechanical (one CMK + IAM grant
per tenant, application code already reads the alias, switch to a
tenant-resolution function for the alias).

## Alternatives considered

1. **Per-tenant CMK from day one.** What ADR-001 originally claimed.
   Operationally painful at our 1-FTE scale:
   - One IAM policy per CMK (cardinality = # tenants); we'd have
     dozens to maintain by the time we hit 50 customers.
   - Onboarding a new tenant requires a Terraform apply (or runtime
     `kms:CreateKey` API call), adding a state-management dimension
     to every signup.
   - The kill-switch property "customer revokes by disabling their
     CMK" is real, but no customer has asked for it yet — building
     the feature ahead of demand is YAGNI.
   - Deferred to the point at which the first regulated customer
     contractually requires it.
2. **AWS-managed key (aws/kms default).** Cheaper, no IAM policy to
   maintain, no rotation knob. Rejected — `aws/kms` keys are shared
   across the AWS account boundary in ways we can't audit, and we
   lose the "kms:DisableKey kill switch" property because we don't
   own the key's lifecycle.
3. **Customer-Managed BYOK** (customer pastes their own key
   material in). Strongest sovereignty story; rejected for now
   because (a) we have no customer asking, (b) AWS KMS XKS adds
   significant operational complexity, (c) key custody questions
   become contractual rather than technical.
4. **No envelope encryption at all** (rely on RDS storage encryption
   only). Rejected — loses the application-visible kill-switch and
   the per-decryption CloudTrail evidence the brutal review treats
   as table stakes for a regulated-customer pitch.

## Consequences

* **Positive**
  - One CMK per region = one IAM policy to maintain, one rotation
    cron to monitor, one CloudTrail event class to watch.
  - `kms:DisableKey` is a real kill switch — disabling the key
    instantly fails every subsequent receipt decrypt in the region.
  - Application reads the alias (`alias/aegis-audit-envelope`), not
    the key id, so rotation never touches code.
  - EU and ap-south-1 stacks share no key material (per ADR + per
    data-residency commitments).
* **Negative**
  - No per-tenant revocation. If Tenant X demands "stop reading my
    audit envelope," the answer today is "we'd have to disable the
    whole region's CMK, which affects every tenant in the region."
    Mitigated only by the migration plan above + the existing
    physical-isolation properties.
  - One CMK is one blast radius. A leaked EC2 IAM role can Decrypt
    every region-resident receipt envelope until the role is rotated.
    Mitigated by (a) the 30-day deletion window, (b) CloudTrail
    forensics, (c) the no-tenant-plaintext-in-DB-anyway property
    from ADR-001.
* **Reversibility**
  - **Migration to per-tenant CMKs is mechanical** when needed: add
    a per-tenant alias creation step to the signup flow, update the
    application's alias-resolution helper to read tenant context.
    1-2 sprints when the contract lands.

## Implementation references

* `infra/terraform/modules/audit_kms/main.tf:12-29` — CMK + alias +
  rotation + deletion-window setup
* `infra/terraform/modules/audit_kms/main.tf:45-60` — IAM policy
  grant to the EC2 instance role
* `services/audit/signer.py:236` — application reads
  `RECEIPT_SIGNING_PROVIDER=kms` to enable envelope encryption
* `infra/terraform/envs/eu-west-1/terraform.tfvars.example` — EU
  instance gets its own CMK in eu-west-1 (no cross-region sharing)
* `docs/runbooks/secrets_rotation.md` — rotation cadence + procedure
* `docs/security/data_residency.md` §3 — KMS keys never cross regions

## Verification

```bash
# 1. Confirm exactly ONE customer-managed CMK per region with the
#    expected alias.
aws kms list-aliases --region ap-south-1 \
  --query 'Aliases[?starts_with(AliasName, `alias/aegis-audit-envelope`)]'
# expect: 1 alias

# 2. Confirm automatic rotation is enabled.
aws kms get-key-rotation-status --region ap-south-1 \
  --key-id alias/aegis-audit-envelope --query 'KeyRotationEnabled'
# expect: true

# 3. Sanity-check the kill switch is reachable to the operator (but
#    do NOT actually run it — it would brick every receipt envelope
#    in the region).
aws iam simulate-principal-policy \
  --policy-source-arn "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):role/<operator-role>" \
  --action-names kms:DisableKey \
  --resource-arns "arn:aws:kms:ap-south-1:*:key/$(aws kms describe-key --key-id alias/aegis-audit-envelope --query 'KeyMetadata.KeyId' --output text)" \
  --query 'EvaluationResults[0].EvalDecision'
# expect: "allowed"
```
