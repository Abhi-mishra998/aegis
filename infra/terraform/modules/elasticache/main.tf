# ElastiCache Redis — primary + N-1 replicas, NO cluster mode.
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
  description          = "Aegis Redis replication group — primary + ${var.num_nodes - 1} replica(s)."
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
  # auth_token not set — TLS + private subnets + SG already restrict to
  # EC2 only. Auth token adds rotation burden without raising the bar
  # at this stage. Add when first F500 customer asks.

  snapshot_retention_limit = 7
  snapshot_window          = "20:30-21:30" # UTC 20:30-21:30 = 02:00-03:00 IST

  tags = {
    Name = "${var.name_prefix}-redis"
  }
}
