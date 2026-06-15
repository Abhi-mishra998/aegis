# Sprint 9 — Multi-AZ Redis replication group.

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-redis-subnets"
  subnet_ids = var.subnet_ids
  tags       = var.tags
}

data "aws_secretsmanager_secret_version" "auth_token" {
  count     = var.auth_token_secret_arn != null ? 1 : 0
  secret_id = var.auth_token_secret_arn
}

resource "aws_elasticache_replication_group" "this" {
  replication_group_id = var.replication_group_id
  description          = var.description

  engine               = "redis"
  engine_version       = var.engine_version
  node_type            = var.node_type
  port                 = 6379
  parameter_group_name = var.parameter_group_name

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = var.security_group_ids

  # HA — multi-AZ + automatic failover + at least one replica per shard.
  multi_az_enabled           = var.replicas_per_node_group >= 1
  automatic_failover_enabled = var.replicas_per_node_group >= 1

  num_node_groups         = var.num_node_groups
  replicas_per_node_group = var.replicas_per_node_group

  snapshot_retention_limit = var.snapshot_retention_limit_days
  snapshot_window          = "01:00-02:00"

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = var.kms_key_id

  auth_token = var.auth_token_secret_arn != null ? data.aws_secretsmanager_secret_version.auth_token[0].secret_string : null

  apply_immediately = false

  tags = merge(var.tags, {
    Name = var.replication_group_id
  })
}
