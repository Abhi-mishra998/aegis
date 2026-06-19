# Migration — old `prod-ha` Terraform to v2

This is the runnable bash for the zero-downtime swap from
`infra/terraform.old/environments/prod-ha/` to the v2 stack at
`infra/terraform/`. Spec lives at `/terraform.md` §10.

**Customer-facing goal:** no 502 during the swap.
**Operator time:** ~3 hours including the 30-minute v2-smoke window.

---

## T-2 hours — prep + snapshot

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp

# Take a labelled RDS snapshot of the live database.
TS=$(date -u +%Y%m%d-%H%M%S)
aws rds create-db-snapshot \
  --region ap-south-1 \
  --db-snapshot-identifier acp-prodha-postgres-pre-tf-rebuild-${TS} \
  --db-instance-identifier acp-prodha-postgres

# Dump the OLD terraform state for rollback evidence.
mkdir -p /tmp/aegis-rebuild-${TS}
cd infra/terraform.old/environments/prod-ha
terraform state pull > /tmp/aegis-rebuild-${TS}/old-state.json
cd -

# Capture the live resource ARNs we will preserve.
cat > /tmp/aegis-rebuild-${TS}/preserved.txt <<EOF
ACM cert:     $(aws acm list-certificates --region ap-south-1 \
                 --query 'CertificateSummaryList[?DomainName==`aegisagent.in`].CertificateArn' \
                 --output text)
Route 53 zone: Z033117538JKIIKDBDPUJ  aegisagent.in.
Public roots:  s3://aegis-public-roots-628478946931
State bucket:  s3://aegis-terraform-state-628478946931
Bundle bucket: s3://acp-backups-prodha-628478946931
EOF
cat /tmp/aegis-rebuild-${TS}/preserved.txt

# Lower Route 53 TTL so the apex swap propagates fast.
# Look up the current record set then update to TTL=60.
# (Done manually via console or `aws route53 change-resource-record-sets`.)
```

---

## T-0 — the swap

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp/infra/terraform

# 1. Initialise the new backend (S3 native locking, no DynamoDB).
terraform init

# 2. Sanity-check the build with no AWS calls.
terraform validate

# 3. Plan + save. Expect ~45 resources to create.
terraform plan -var-file=envs/prod/terraform.tfvars -out=/tmp/tfplan-v2

# 4. Apply. This builds VPC, ALB, ASG, RDS (NEW empty), Redis in parallel.
#    RDS will be the bottleneck (~10 min). Watch for any timeout.
terraform apply /tmp/tfplan-v2

# 5. The new ALB DNS is in the outputs.
ALB_DNS=$(terraform output -raw alb_dns_name)
echo "v2 ALB: ${ALB_DNS}"

# 6. Add a Route 53 TEST record so we can smoke v2 without touching apex.
aws route53 change-resource-record-sets \
  --hosted-zone-id Z033117538JKIIKDBDPUJ \
  --change-batch '{
    "Changes": [{
      "Action": "UPSERT",
      "ResourceRecordSet": {
        "Name": "v2.aegisagent.in",
        "Type": "CNAME",
        "TTL": 60,
        "ResourceRecords": [{"Value": "'"${ALB_DNS}"'"}]
      }
    }]
  }'

# 7. Restore the RDS snapshot from T-2 into the NEW RDS. Two options:
#    a. (preferred) Restore in-place: aws rds restore-db-instance-from-db-snapshot
#       then `terraform import module.rds.aws_db_instance.main aegis-prod-postgres`
#    b. (alternative) Take a fresh logical pg_dump from old → restore into new.
#       Cleaner for major-version upgrades; slower for big DBs.
```

---

## Smoke v2 (run from any machine)

Verify the 10 essentials from `setup-agies.md` Section 11 against
`v2.aegisagent.in`:

```bash
curl -fsS https://v2.aegisagent.in/status                 # 12 components operational
curl -fsS https://v2.aegisagent.in/api/health             # 200
curl -fsS https://v2.aegisagent.in/v1/messages -H "auth:..." -d "..."  # path B
# ... etc — the full 10
```

Watch CloudWatch for 30 minutes. Zero 5xx is the green.

---

## Flip apex to v2

```bash
# Route 53 — apex A and AAAA aliases now point at the NEW ALB.
# (The route53 module in v2 already created these records targeting the
# NEW ALB; the issue is they conflict with the old records.)
#
# If the new apex records are blocked by the existing ones from the old
# stack: temporarily delete the old records first, then `terraform apply`
# will write the new ones.

# Or do it manually in one batch:
aws route53 change-resource-record-sets \
  --hosted-zone-id Z033117538JKIIKDBDPUJ \
  --change-batch file:///tmp/aegis-rebuild-${TS}/apex-flip.json
```

Existing connections drain within 60s (the TTL we set at T-2).

Watch v2 for 10 minutes. Verify no 5xx through the cutover.

---

## T+1 hour — tear down old

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp/infra/terraform.old/environments/prod-ha
terraform destroy

# The destroy MUST skip aegis-public-roots-628478946931 — it's marked
# prevent_destroy in the new s3 module but the old module didn't carry
# that. Confirm by listing the bucket BEFORE running destroy:
aws s3 ls s3://aegis-public-roots-628478946931/

# After destroy completes, list it again — must still be there.
aws s3 ls s3://aegis-public-roots-628478946931/

# Archive the old terraform code (don't delete — git history covers it).
cd ..
git mv terraform.old "_archive/terraform.old.$(date +%Y%m%d)"
git commit -m "archive: legacy prod-ha terraform after v2 migration"

# Restore Route 53 TTL on apex records back to 300 (the default).
```

---

## Rollback if v2 misbehaves before the apex flip

Nothing happened to production. The old stack is still serving.
Delete the v2 stack and try again:

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp/infra/terraform
terraform destroy -var-file=envs/prod/terraform.tfvars
```

## Rollback if v2 misbehaves AFTER the apex flip

1. Flip Route 53 apex back to the OLD ALB DNS (60s propagation).
2. Investigate v2 with traffic now back on the old stack.
3. Do NOT `terraform destroy` v2 yet — the resources hold the clue.

---

## Verify the v2 stack post-apply

```bash
cd /Users/abhishekmishra/mcp-security-controller/acp/infra/terraform
terraform output

# Should print: alb_dns_name, vpc_id, asg_name, rds_endpoint,
# redis_primary_endpoint, secrets_arn_*, sns_alarm_topic_arn,
# waf_web_acl_arn, ssm_bundle_parameter_name, bundle_bucket.

# Promote the first real bundle (post-restore):
BUNDLE_SHA=$(git -C /Users/abhishekmishra/mcp-security-controller/acp \
              rev-parse --short HEAD)
bash /Users/abhishekmishra/mcp-security-controller/acp/scripts/ops/build_release_bundle.sh

aws s3 cp /tmp/aegis-bundle-*.tar.gz \
  s3://acp-backups-prodha-628478946931/releases/bundle-${BUNDLE_SHA}.tar.gz

aws ssm put-parameter \
  --name /aegis/prod/current_bundle_sha \
  --value "${BUNDLE_SHA}" --overwrite

aws autoscaling start-instance-refresh \
  --auto-scaling-group-name $(terraform output -raw asg_name) \
  --preferences MinHealthyPercentage=100,InstanceWarmup=300
```

The new instances boot off bundle-${BUNDLE_SHA}.tar.gz. Old instances
drain only after new are healthy (MinHealthyPercentage=100 — the fix
for yesterday's outage cascade).
