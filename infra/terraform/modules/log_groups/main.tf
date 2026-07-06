# CloudWatch log groups for the application + supporting services.
# Created upfront so the CW agent on EC2 has a writable target on first
# boot — otherwise it logs into a default group with infinite retention.
#
# Retention is bounded; budget visibility matters more than long-tail
# debug here.

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/aegis/agent"
  retention_in_days = 14
  skip_destroy      = false

  tags = {
    Name = "${var.name_prefix}-agent-log"
  }
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/aegis/gateway"
  retention_in_days = 14

  tags = {
    Name = "${var.name_prefix}-gateway-log"
  }
}

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/aegis/audit"
  retention_in_days = 30 # audit-layer logs kept longer; still bounded

  tags = {
    Name = "${var.name_prefix}-audit-log"
  }
}

# Pre-created RDS export log groups so retention is bounded. If we skip
# these, RDS auto-creates them with INFINITE retention (the 56 GB bleed
# we saw on aegis-prod-postgres). Names must match the RDS instance ID
# exactly; keep in sync with modules/rds/main.tf identifier.
# ponytail: name coupling with rds module — if rds identifier changes, update here.
resource "aws_cloudwatch_log_group" "rds_postgres" {
  name              = "/aws/rds/instance/${var.name_prefix}-postgres/postgresql"
  retention_in_days = 14

  tags = {
    Name = "${var.name_prefix}-rds-postgres-log"
  }
}

resource "aws_cloudwatch_log_group" "rds_upgrade" {
  name              = "/aws/rds/instance/${var.name_prefix}-postgres/upgrade"
  retention_in_days = 14

  tags = {
    Name = "${var.name_prefix}-rds-upgrade-log"
  }
}
