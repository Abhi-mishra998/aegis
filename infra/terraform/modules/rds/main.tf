# Postgres Multi-AZ + parameter group + subnet group + automated backups.
#
# Master password sourced from Secrets Manager (manage_master_user_password
# would be cleaner but pins to AWS-managed secret — we want our own ARN
# so the EC2 role grant in iam module stays narrow).

data "aws_secretsmanager_secret_version" "master_password" {
  secret_id = var.master_password_secret
}

# AWS-managed KMS key alias for RDS — used for Performance Insights
# at-rest encryption (free; satisfies tfsec aws-rds-encrypt-instance-storage-data).
data "aws_kms_alias" "rds" {
  name = "alias/aws/rds"
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-db-subnets"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.name_prefix}-db-subnets"
  }
}

resource "aws_db_parameter_group" "main" {
  name        = "${var.name_prefix}-pg15"
  family      = "postgres15"
  description = "Aegis Postgres 15 parameter group - log_statement none, force_ssl on."

  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }

  parameter {
    name  = "log_connections"
    value = "1"
  }

  parameter {
    name  = "log_disconnections"
    value = "1"
  }

  tags = {
    Name = "${var.name_prefix}-pg15"
  }
}

resource "aws_db_instance" "main" {
  identifier     = "${var.name_prefix}-postgres"
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "aegis"
  username = "aegis"
  password = data.aws_secretsmanager_secret_version.master_password.secret_string

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [var.rds_security_group]
  parameter_group_name   = aws_db_parameter_group.main.name
  port                   = 5432

  multi_az            = var.multi_az
  publicly_accessible = false

  backup_retention_period   = var.backup_retention
  backup_window             = "20:00-21:00" # UTC 20-21 = 01:30-02:30 IST off-peak
  maintenance_window        = "sat:21:30-sat:22:30"
  copy_tags_to_snapshot     = true
  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name_prefix}-postgres-final-${formatdate("YYYYMMDD-hhmmss", timestamp())}"
  deletion_protection       = true

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  # Performance Insights uses the AWS-managed KMS for at-rest encryption
  # by default. Setting an explicit kms key keeps the tfsec scan happy
  # without committing to a CMK.
  performance_insights_enabled    = true
  performance_insights_kms_key_id = data.aws_kms_alias.rds.target_key_arn
  monitoring_interval             = 0 # set > 0 if Enhanced Monitoring needed

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = {
    Name = "${var.name_prefix}-postgres"
  }

  # final_snapshot_identifier timestamp() changes on every plan, so
  # ignore the diff. The snapshot itself is taken at destroy time.
  lifecycle {
    ignore_changes = [final_snapshot_identifier, password]
  }
}
