# Production import runbook

The prod environment's `main.tf` describes every live resource at
`aegisagent.in`. **Before `terraform apply`** every resource must be
imported so Terraform claims the existing ID instead of trying to create
a duplicate.

This file records the per-resource import commands. Run them in order;
each is idempotent (Terraform will refuse to re-import an already-managed
resource).

## 0. Bootstrap state backend (once per account)

```bash
cd infra/terraform/bootstrap
terraform init
terraform apply       # creates s3://aegis-terraform-state + DDB lock table
```

## 1. Init the prod workspace

```bash
cd ../environments/prod
terraform init
```

## 2. Import network resources

```bash
terraform import 'module.network.aws_vpc.this' vpc-0b86b702b936fc905
terraform import 'module.network.aws_internet_gateway.this' <find-via-aws-ec2-describe-internet-gateways>
# Subnets — index follows availability_zones order (1a then 1b):
terraform import 'module.network.aws_subnet.public[0]'  subnet-00ce70dbbbe9602f1
terraform import 'module.network.aws_subnet.public[1]'  subnet-0b808f72efc46dff2
terraform import 'module.network.aws_subnet.private[0]' subnet-01baf8689d58a521d
terraform import 'module.network.aws_subnet.private[1]' subnet-0a32990f24a17d8a2
# Route tables — find IDs via:
#   aws ec2 describe-route-tables --filters Name=vpc-id,Values=vpc-0b86b702b936fc905
terraform import 'module.network.aws_route_table.public'  rtb-<find>
terraform import 'module.network.aws_route_table.private' rtb-<find>
# Route-table associations (4 of them):
terraform import 'module.network.aws_route_table_association.public[0]'  <subnet>/<rtb>
# ... repeat for [1], private[0], private[1]
```

## 3. Import security groups

```bash
terraform import 'module.security_groups.aws_security_group.alb'   sg-0c50d69ba3de40bf3
terraform import 'module.security_groups.aws_security_group.ec2'   sg-0e8b5bdd4d4a0d9b0
terraform import 'module.security_groups.aws_security_group.rds'   sg-0e72625fc48706b98
terraform import 'module.security_groups.aws_security_group.redis' sg-00e47aee22e90ae33
```

## 4. Import IAM

```bash
terraform import 'module.iam.aws_iam_role.ec2'                       acp-ec2-role
terraform import 'module.iam.aws_iam_instance_profile.ec2'           acp-ec2-role
terraform import 'module.iam.aws_iam_role_policy_attachment.ssm' \
    'acp-ec2-role/arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore'
terraform import 'module.iam.aws_iam_role_policy_attachment.cloudwatch_agent' \
    'acp-ec2-role/arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy'

# NOTE: the live role also has AmazonS3FullAccess attached. The module
# does NOT declare that attachment — we want to remove it as a sprint-8.5
# hardening (scope to specific bucket ARNs via the s3_backup module input
# instead). Drop the live attachment AFTER the new scoped policy is in
# place:
aws iam detach-role-policy --role-name acp-ec2-role \
    --policy-arn arn:aws:iam::aws:policy/AmazonS3FullAccess
```

## 5. Import EC2 instances

```bash
terraform import 'module.compute.aws_instance.app[0]' i-066b1e9043c465dfd  # 1a
terraform import 'module.compute.aws_instance.app[1]' i-00a0c0ed1155e6ffd  # 1b
```

## 6. Import RDS

```bash
# Master password — create the secret FIRST so the module can read it.
aws secretsmanager create-secret \
    --name acp/rds_master_password \
    --description "Imported from live" \
    --secret-string "<current-prod-password-from-rds-credentials>"

terraform import 'module.rds.aws_db_subnet_group.this' acp-rds-subnet-group
terraform import 'module.rds.aws_db_instance.this'    acp-postgres-prod
```

## 7. Import ElastiCache

```bash
terraform import 'module.elasticache.aws_elasticache_subnet_group.this' acp-redis-subnet-group
terraform import 'module.elasticache.aws_elasticache_cluster.this'      acp-redis-prod
```

## 8. Import ACM cert

```bash
terraform import 'module.acm.aws_acm_certificate.this' \
    arn:aws:acm:ap-south-1:628478946931:certificate/74f2f769-095c-4a7f-9799-a1ce9604992e
# Cert validation records — these are Route53 CNAMEs created by ACM:
# Find them via `aws route53 list-resource-record-sets --hosted-zone-id Z033117538JKIIKDBDPUJ`
# and import each via:
terraform import 'module.acm.aws_route53_record.validation["aegisagent.in"]' \
    Z033117538JKIIKDBDPUJ_<validation-record-name>_CNAME
# repeat for www.aegisagent.in and api.aegisagent.in
```

## 9. Import ALB

```bash
terraform import 'module.alb.aws_lb.this' \
    arn:aws:elasticloadbalancing:ap-south-1:628478946931:loadbalancer/app/acp-alb/2bf2c7f1cc13ddf7
terraform import 'module.alb.aws_lb_target_group.this' \
    arn:aws:elasticloadbalancing:ap-south-1:628478946931:targetgroup/acp-ui-tg/1aef662f1157662a
# Listeners — find ARNs via `aws elbv2 describe-listeners --load-balancer-arn ...`
terraform import 'module.alb.aws_lb_listener.https'         <https-listener-arn>
terraform import 'module.alb.aws_lb_listener.http_redirect' <http-listener-arn>
# Target group attachments (one per EC2 instance):
terraform import 'module.alb.aws_lb_target_group_attachment.instances[0]' \
    <target-group-arn>/i-066b1e9043c465dfd
terraform import 'module.alb.aws_lb_target_group_attachment.instances[1]' \
    <target-group-arn>/i-00a0c0ed1155e6ffd
```

## 10. Import Route53 alias records

```bash
terraform import 'module.route53.aws_route53_record.alias["aegisagent.in"]'    \
    Z033117538JKIIKDBDPUJ_aegisagent.in_A
terraform import 'module.route53.aws_route53_record.alias["api.aegisagent.in"]' \
    Z033117538JKIIKDBDPUJ_api.aegisagent.in_A
```

## 11. Import S3 buckets

```bash
terraform import 'module.s3.aws_s3_bucket.this["backups"]'     acp-backups-prod-am
terraform import 'module.s3.aws_s3_bucket.this["backups_alt"]' acp-backups-abhishek-prod
terraform import 'module.s3.aws_s3_bucket.this["statuspage"]'  aegis-statuspage   # CREATE first if missing
# The versioning + SSE + public-access-block resources usually need to be
# imported too:
terraform import 'module.s3.aws_s3_bucket_versioning.this["backups"]'    acp-backups-prod-am
terraform import 'module.s3.aws_s3_bucket_server_side_encryption_configuration.this["backups"]' acp-backups-prod-am
terraform import 'module.s3.aws_s3_bucket_public_access_block.this["backups"]' acp-backups-prod-am
# repeat for backups_alt and statuspage
```

## 12. Import Secrets Manager entries (if they already exist)

```bash
terraform import 'module.secrets.aws_secretsmanager_secret.this["jwt_secret_key"]'     acp/jwt_secret_key
terraform import 'module.secrets.aws_secretsmanager_secret.this["internal_secret"]'    acp/internal_secret
terraform import 'module.secrets.aws_secretsmanager_secret.this["rds_master_password"]' acp/rds_master_password
terraform import 'module.secrets.aws_secretsmanager_secret.this["groq_api_key"]'       acp/groq_api_key
```

## 13. Verify plan is empty

```bash
terraform plan
# Expected: "No changes. Your infrastructure matches the configuration."
```

If `terraform plan` shows changes, the .tf is drifting from reality.
**Fix the .tf** to match the live state (do NOT apply blindly), then
re-run plan until clean.

## 14. (Optional) Lock in by applying once

Once `terraform plan` is empty, an empty `terraform apply` rotates the
state-version timestamp and confirms write access to the backend:

```bash
terraform apply
# Expected: "Apply complete! Resources: 0 added, 0 changed, 0 destroyed."
```

## What's NOT imported (manual cleanup recommended)

| Live resource | Why excluded |
|----|----|
| `vpc-089e6e43e7874a3d6` (172.31.0.0/16) | Default VPC — unused by Aegis; delete it manually for hygiene |
| `subnet-06a2d7f951c456092`, `subnet-084728aa26dffd3df`, `subnet-09a2e510dbe9ad898` | Default-VPC subnets — same as above |
| `acp-fix-1779860735` S3 bucket | Looks like a debugging artifact — verify and delete |
| Various `_acm-validations.aws` Route53 CNAMEs | Old cert validation records — clean up after a cert rotation |
| `0.0.0.0/0` SSH ingress on `acp-ec2-sg` | Open SSH to the world. Narrow to operator CIDRs only. |
