# S3 Object Lock — migration runbook for the pre-existing buckets

Sprint EH-5 enabled `object_lock_enabled = true` on the `backups` and `cloudtrail` buckets in Terraform. AWS does not allow flipping Object Lock on an **existing** bucket — only at creation. The terraform change therefore applies cleanly to greenfield deploys; the in-flight production buckets need this one-shot migration.

## Why we want this

| Bucket | Without Object Lock | With Object Lock |
|--------|---------------------|------------------|
| `aegis-prod-backups-…` | An admin with bucket-write IAM can `aws s3 rm --recursive` and the versions live another 90 days (lifecycle), then disappear | GOVERNANCE mode = even bypass-perm admin needs to explicitly clear retention before delete; COMPLIANCE mode = nobody, ever |
| `aegis-prod-cloudtrail-…` | Same — an attacker who steals root credentials wipes the trail of their own intrusion | COMPLIANCE mode = trail outlives the attacker for the retention window |
| `aegis-public-roots-…` | Same | We can argue to a regulator that the customer-verifiable transparency archive cannot have been silently rewritten |

## Migration procedure (per bucket)

Run during a low-traffic window. The window is needed because reads from the bucket during the swap point at the old bucket until the alias flip.

```bash
OLD=aegis-prod-backups-628478946931
NEW=aegis-prod-backups-628478946931-v2
REGION=ap-south-1

# 1. Create the replacement bucket WITH Object Lock from creation
aws s3api create-bucket \
  --bucket "$NEW" \
  --region "$REGION" \
  --create-bucket-configuration LocationConstraint=$REGION \
  --object-lock-enabled-for-bucket

aws s3api put-bucket-versioning --bucket "$NEW" \
  --versioning-configuration Status=Enabled

aws s3api put-object-lock-configuration --bucket "$NEW" \
  --object-lock-configuration '{
    "ObjectLockEnabled": "Enabled",
    "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 30}}
  }'

aws s3api put-bucket-encryption --bucket "$NEW" \
  --server-side-encryption-configuration '{
    "Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]
  }'

aws s3api put-public-access-block --bucket "$NEW" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

# 2. Mirror the data (preserves all versions)
aws s3 sync "s3://${OLD}/" "s3://${NEW}/" --no-progress

# 3. Sanity-check: object count + total size match
aws s3api list-objects-v2 --bucket "$OLD" --query 'sum(Contents[].Size)'
aws s3api list-objects-v2 --bucket "$NEW" --query 'sum(Contents[].Size)'

# 4. Swap the terraform reference. In envs/prod/terraform.tfvars:
#      bundle_bucket = "aegis-prod-backups-628478946931-v2"
#    Re-apply:
cd infra/terraform && terraform apply

# 5. Roll the ASG so the user_data fetches from the new bucket
aws autoscaling start-instance-refresh \
  --auto-scaling-group-name aegis-prod-asg \
  --preferences MinHealthyPercentage=100,InstanceWarmup=300

# 6. After 7 days of confirmed clean traffic, delete the old bucket
aws s3 rb "s3://${OLD}" --force
```

## CloudTrail-specific note

Recreating the CloudTrail bucket requires re-pointing the trail:

```bash
aws cloudtrail update-trail \
  --name aegis-prod-mgmt-events \
  --s3-bucket-name aegis-prod-cloudtrail-628478946931-v2
```

After that, any event sent during the swap window goes to the new bucket. There is a ~2-minute gap where events go to the old bucket; CloudTrail's S3 delivery is at-least-once so duplicates may appear after the swap — dedup by `eventID`.

## Public-roots bucket

The transparency-log bucket is the most sensitive. Migration follows the same pattern but with extra care:

```bash
OLD=aegis-public-roots-628478946931
NEW=aegis-public-roots-628478946931-v2

# Use COMPLIANCE mode (no admin bypass) and a longer retention
aws s3api create-bucket --bucket "$NEW" --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1 \
  --object-lock-enabled-for-bucket

aws s3api put-object-lock-configuration --bucket "$NEW" \
  --object-lock-configuration '{
    "ObjectLockEnabled": "Enabled",
    "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 365}}
  }'

# Public-read policy (same as old bucket)
aws s3api put-bucket-policy --bucket "$NEW" --policy file://infra/terraform/modules/s3/public_roots_policy.json

aws s3 sync "s3://${OLD}/" "s3://${NEW}/" --no-progress
```

**Critical:** before customers update their verifier configs to the new bucket, publish a signed "bucket-migration" marker in `latest.json` at both old + new locations so existing AEVF verifiers find both. See `services/audit/transparency_scheduler.py` for the publish path.

## Acceptance criteria

- [ ] `aws s3api get-object-lock-configuration --bucket <new>` returns the expected retention policy.
- [ ] `aws s3api delete-object --bucket <new> --key <existing-key>` returns `AccessDenied` (Object Lock retention blocks delete during the retention window).
- [ ] `aws s3 sync` from old → new is byte-for-byte identical (`aws s3api head-object` returns matching ETag for every key).
- [ ] Old bucket's last day of traffic was at least 7 days ago before `s3 rb` is run.
- [ ] Run `bash docs/runbooks/disaster_recovery.md §8` end-to-end and confirm a fresh restore drill against the new bucket completes successfully.
