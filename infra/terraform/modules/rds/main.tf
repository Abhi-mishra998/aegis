# Single RDS PostgreSQL instance. Multi-AZ is a per-environment toggle so
# the dev tier can stay at one node for cost while prod gets standby.

resource "aws_db_subnet_group" "this" {
  name       = "${var.name_prefix}-rds-subnet-group"
  subnet_ids = var.subnet_ids
  tags       = merge(var.tags, { Name = "${var.name_prefix}-rds-subnet-group" })
}

# Read the master password from Secrets Manager so it never appears in tfstate.
data "aws_secretsmanager_secret_version" "master_password" {
  secret_id = var.master_password_secret_arn
}

resource "aws_db_instance" "this" {
  identifier = var.identifier
  engine     = "postgres"
  # Pinning the full minor (15.x) prevents the auto-upgrade-on-Sunday
  # surprise. Operators bump deliberately.
  engine_version    = var.engine_version
  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage_gb
  # max_allocated_storage triggers RDS storage auto-scaling.
  max_allocated_storage = var.max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  multi_az = var.multi_az

  db_name  = var.db_name
  username = var.master_username
  password = data.aws_secretsmanager_secret_version.master_password.secret_string

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = var.vpc_security_group_ids
  publicly_accessible    = false

  backup_retention_period = var.backup_retention_period_days
  backup_window           = "20:00-21:00"
  maintenance_window      = "sun:22:00-sun:23:00"

  performance_insights_enabled    = true
  monitoring_interval             = var.enhanced_monitoring_interval_seconds
  monitoring_role_arn             = var.enhanced_monitoring_interval_seconds > 0 ? var.monitoring_role_arn : null
  enabled_cloudwatch_logs_exports = ["postgresql"]

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "${var.identifier}-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"

  apply_immediately = false # respect maintenance window

  tags = merge(var.tags, { Name = var.identifier })

  lifecycle {
    ignore_changes = [
      final_snapshot_identifier, # timestamp churn
    ]
  }
}
