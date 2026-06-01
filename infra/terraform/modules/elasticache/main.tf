# Single-node Redis cluster.
# Multi-AZ failover requires switching to `aws_elasticache_replication_group`
# with >= 2 cache_clusters — that's a deliberate prod-only choice when
# downtime tolerance shrinks. For dev (10 users) a single node is fine.

resource "aws_elasticache_subnet_group" "this" {
  name       = "${var.name_prefix}-redis-subnet-group"
  subnet_ids = var.subnet_ids
  tags       = merge(var.tags, { Name = "${var.name_prefix}-redis-subnet-group" })
}

resource "aws_elasticache_cluster" "this" {
  cluster_id           = var.cluster_id
  engine               = "redis"
  engine_version       = var.engine_version
  node_type            = var.node_type
  num_cache_nodes      = var.num_cache_nodes
  parameter_group_name = "default.redis7"
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.this.name
  security_group_ids = var.security_group_ids

  snapshot_retention_limit = var.snapshot_retention_limit_days
  snapshot_window          = var.snapshot_retention_limit_days > 0 ? "21:00-22:00" : null

  apply_immediately = false

  tags = merge(var.tags, { Name = var.cluster_id })
}
