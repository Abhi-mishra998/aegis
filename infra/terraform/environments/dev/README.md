# Aegis `dev` environment — 10-user test portal

Sized for ~10 users testing the platform. **Target spend: ~$55/month**
(ap-south-1, on-demand, no Reserved Instances).

## Resource inventory + cost (ap-south-1)

| Resource | Instance | Quantity | Monthly cost |
|----------|----------|----------|-------------:|
| EC2 | t3.small (2 vCPU / 2 GB) | 1 | ~$15 |
| RDS | db.t4g.micro, single-AZ, 20 GB gp3 | 1 | ~$13 |
| ElastiCache | cache.t3.micro, single node | 1 | ~$11 |
| ALB | application LB | 1 | ~$16 (fixed) |
| S3 (3 buckets) | < 5 GB combined | — | < $1 |
| Route 53 | 1 hosted zone + records | — | $0.50 |
| Data transfer | < 1 GB/month assumed | — | < $1 |
| **Total** | | | **~$55** |

The budget alert fires at 80% and 100% of a $60 cap via SNS → email. Edit
`budget_alert_emails` in `variables.tf` to redirect.

## Apply

```bash
# Bootstrap once per account (creates state backend):
cd ../../bootstrap && terraform init && terraform apply

# Then apply the dev environment:
cd ../environments/dev
terraform init
terraform plan
terraform apply
```

The first apply will:
1. Create the VPC + subnets + IGW + route tables.
2. Create the 4 security groups (alb, ec2, rds, redis).
3. Create the IAM role + instance profile (scoped to the dev backup bucket).
4. Create 5 empty Secrets Manager entries — **YOU MUST POPULATE THEM
   BEFORE THE APPLY OF THE NEXT STEP**:
   ```bash
   aws secretsmanager put-secret-value \
     --secret-id acp-dev/rds_master_password \
     --secret-string "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
   ```
5. Provision RDS (uses the master_password secret).
6. Provision Redis.
7. Provision the EC2 host (1× t3.small).
8. Provision the ALB + ACM cert + Route 53 alias.

## Post-apply

See `terraform output next_steps` for the operator checklist.

## Even cheaper (~$35/month)

If $55/month is still too much for a pure throw-away test:

1. **Drop the ALB.** Expose the EC2 public IP directly. Saves ~$16/month.
   - Modify `dev/main.tf` to comment out the `module "alb"` block.
   - Modify `dev/main.tf` Route53 record to point at the EC2 public IP
     instead of the ALB DNS name.
   - Trade-off: no TLS termination at the edge; you'd need to run
     certbot on the EC2 host directly, OR access via the EC2 IP only
     for the 10 users.

2. **Drop Redis.** If your test scenario doesn't exercise the token
   cache or kill switch, you can run `docker compose` without Redis
   and the gateway falls open (sprint-1 LRU revocation latency comment
   describes the failure mode). Saves ~$11/month.

3. **Stop the stack when not in use.**
   `aws ec2 stop-instances --instance-ids <id>` between test sessions.
   You only pay for storage (~$2/month) when stopped. The RDS instance
   can also be stopped for up to 7 days at a stretch.

Combining all three lands at ~$15/month.

## Tear down

```bash
terraform destroy
```

Because `deletion_protection = false` and `skip_final_snapshot = true`,
the dev environment tears down cleanly in ~10 minutes. Secrets Manager
entries have a 7-day recovery window — if you destroyed by accident,
`aws secretsmanager restore-secret --secret-id ...` brings them back.
