# Declarative imports (Terraform 1.5+).
#
# At apply time, Terraform finds the named resource already exists and
# adopts it into state instead of creating a duplicate. After the first
# successful apply, this file can be left in place (no-op on later runs)
# or deleted — leaving it is documentation.
#
# To check the current resource attributes are configured exactly as our
# terraform expects:
#
#   terraform plan -var-file=envs/prod/terraform.tfvars
#
# Any drift between the live attributes and our resource block shows up
# as a `change`. If something there is intentional, update our code to
# match. If something there is unintentional, the operator decides:
# overwrite (apply) or fix the live resource first.

import {
  to = module.s3.aws_s3_bucket.public_roots
  id = "aegis-public-roots-628478946931"
}

# Post-teardown recovery (2026-07-25) — the existing `-backups-` and
# `-cloudtrail-` buckets have `object_lock_enabled = false`, which
# terraform now wants set to `true`. That flag is immutable at
# bucket-creation time, so importing them would force terraform to
# DELETE-and-RECREATE — destroying 3 months of pg_dump backups and
# 7 days of cloudtrail audit trail.
#
# Chosen path: leave the pre-teardown buckets in place (data untouched,
# lifecycle policies decide their fate) and let terraform create fresh
# `-v3`-suffixed buckets with Object Lock enabled from the start.
# The `backup_bucket_suffix = "-v3"` in main.tf drives the rename.

# Post-teardown recovery follow-up (2026-07-25) — the RDS PostgreSQL log
# groups are auto-created by AWS the moment the DB enables log exports,
# which happens BEFORE terraform can create them on Phase 1. First apply
# failed with ResourceAlreadyExistsException. Import lets terraform
# adopt the auto-created groups and manage retention from here on.
import {
  to = module.log_groups.aws_cloudwatch_log_group.rds_postgres
  id = "/aws/rds/instance/aegis-prod-postgres/postgresql"
}

import {
  to = module.log_groups.aws_cloudwatch_log_group.rds_upgrade
  id = "/aws/rds/instance/aegis-prod-postgres/upgrade"
}
