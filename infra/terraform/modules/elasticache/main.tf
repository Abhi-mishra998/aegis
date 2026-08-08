# ElastiCache Redis - primary + N-1 replicas, NO cluster mode.
# TLS in-transit and at-rest encryption.

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.name_prefix}-redis-subnets"
  subnet_ids = var.private_subnet_ids

  tags = {
    Name = "${var.name_prefix}-redis-subnets"
  }
}

resource "aws_elasticache_parameter_group" "main" {
  name   = "${var.name_prefix}-redis7"
  family = "redis7"

  # Aegis uses Redis lists (event streams) + sorted sets (cumulative
  # risk windows) + sets (revoked api keys). No need for cluster-mode
  # at design-partner scale.

  tags = {
    Name = "${var.name_prefix}-redis7"
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.name_prefix}-redis"
  description          = "Aegis Redis replication group - primary + ${var.num_nodes - 1} replica(s)."
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.node_type
  num_cache_clusters   = var.num_nodes
  parameter_group_name = aws_elasticache_parameter_group.main.name
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [var.redis_security_group]
  port                 = 6379

  automatic_failover_enabled = var.num_nodes > 1
  multi_az_enabled           = var.num_nodes > 1

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  # M6 fix (31.anaysis.md): AUTH token gates Redis at the protocol layer
  # so a pod compromise (or leaked REDIS_URL) can't PING/SET the
  # kill-switch key or FLUSHALL the state. Required in addition to TLS+SG.
  #
  # COORDINATION TODO before `terraform apply`: every service that reads
  # REDIS_URL must include the auth in the URL, e.g.:
  #     rediss://:<REDIS_AUTH_TOKEN>@<host>:6379/0
  # ASG user_data (infra/terraform/modules/asg/main.tf) must fetch
  # secrets/redis_auth_token via `sec ${var.redis_auth_token_secret_id}`
  # and template `REDIS_AUTH_TOKEN` into infra/.env, then reshape the
  # existing `REDIS_URL=rediss://${REDIS_HOST}/0` line to include it.
  # Apply the terraform + roll a fresh ASG in the same window.
  auth_token = var.redis_auth_token

  snapshot_retention_limit = 7
  snapshot_window          = "20:30-21:30" # UTC 20:30-21:30 = 02:00-03:00 IST

  tags = {
    Name = "${var.name_prefix}-redis"
  }
}
