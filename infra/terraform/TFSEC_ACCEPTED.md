# tfsec — accepted findings

Last scan: 2026-06-19. Result: **89 passed, 19 accepted findings.**

Every finding below has been triaged and accepted as an explicit
design choice. They align with `terraform.md` §13 "What this design
DELIBERATELY leaves out (and why)." Update this file when a finding
moves between accepted and fixed.

---

## Accepted — intentional public exposure (4)

| # | Severity | Rule                                         | Resource                          | Why accepted |
|---|----------|----------------------------------------------|-----------------------------------|--------------|
| 1 | HIGH     | aws-elb-alb-not-public                       | `module.alb.aws_lb.main`          | The ALB is intentionally public — it terminates the customer-facing `aegisagent.in` traffic. This finding is informational. |
| 2 | HIGH×4   | aws-s3-no-public-access-block                | `module.s3.aws_s3_bucket.public_roots` and sub-rules | The transparency bucket is **intentionally** readable by anonymous auditors via `aws s3 ls --no-sign-request`. Adding a public_access_block would break the cryptographic-transparency story. |
| 3 | LOW      | aws-s3-specify-public-access-block           | same                              | Same as above. |

---

## Accepted — AWS-managed KMS instead of CMK (8)

Per `terraform.md` §13: *"AWS-managed KMS is FREE for RDS, S3, Secrets Manager. CMKs only when an F500 demands BYOK."*

| # | Severity | Rule                                         | Resource                                       |
|---|----------|----------------------------------------------|------------------------------------------------|
| 4 | HIGH     | aws-s3-encryption-customer-key               | `module.s3.aws_s3_bucket.alb_logs`             |
| 5 | HIGH     | aws-s3-encryption-customer-key               | `module.s3.aws_s3_bucket.backups`              |
| 6 | HIGH     | aws-s3-encryption-customer-key               | `module.s3.aws_s3_bucket.cloudtrail`           |
| 7 | HIGH     | aws-s3-encryption-customer-key               | `module.s3.aws_s3_bucket.public_roots`         |
| 8 | LOW      | aws-ssm-secret-use-customer-key              | `module.secrets.aws_secretsmanager_secret.db_password` |
| 9 | LOW      | aws-ssm-secret-use-customer-key              | `module.secrets.aws_secretsmanager_secret.jwt_signing` |

**Upgrade path:** when a customer signs an F500 contract requiring BYOK, add a `kms_key_id` variable per module and wire it through. ~1 hour of engineering, no architectural change.

---

## Accepted — observability noise > signal at this stage (5)

| #  | Severity | Rule                                         | Resource             | Why accepted |
|----|----------|----------------------------------------------|----------------------|--------------|
| 10 | MEDIUM   | aws-vpc-no-flow-logs                         | `module.network.aws_vpc.main` | VPC Flow Logs add ~$5/mo CloudWatch ingest and ~$3/mo S3 storage at the volumes we generate. Useful for forensic investigation but rarely consulted at design-partner scale. Turn on at first F500 customer or first security incident. |
| 11 | MEDIUM×4 | aws-s3-enable-bucket-logging                 | each of the 4 created buckets | Bucket-level access logging adds another bucket per source bucket — recursive logs-of-logs. ALB access logs already capture the customer-facing flow; CloudTrail captures the API-layer flow. The remaining bucket-access events at this scale are not worth the storage cost. |

---

## Accepted — RDS hardening deferred (1)

| #  | Severity | Rule                                         | Resource             | Why accepted |
|----|----------|----------------------------------------------|----------------------|--------------|
| 12 | MEDIUM   | aws-rds-enable-iam-auth                      | `module.rds.aws_db_instance.main` | IAM DB auth is excellent for human operators but the application path uses the master password from Secrets Manager. Adding IAM auth requires a token-fetch shim in the application connection pool. Defer until we onboard a second engineer who needs RDS console-via-IAM access. |

---

## Active mitigations applied this scan

- **SNS topic encryption** (`aws_sns_topic.alarms.kms_master_key_id = "alias/aws/sns"`) — added 2026-06-19 in response to `aws-sns-enable-topic-encryption`.
- **RDS Performance Insights KMS** (`performance_insights_kms_key_id`) — added 2026-06-19 in response to `aws-rds-enable-performance-insights-encryption`.

---

## How to re-scan after a change

```bash
cd infra/terraform
tfsec . --soft-fail
```

`--soft-fail` exits 0 regardless of findings — that's fine for our flow since we triage findings in this file rather than fail the apply. To fail-fast in CI, drop the flag.
