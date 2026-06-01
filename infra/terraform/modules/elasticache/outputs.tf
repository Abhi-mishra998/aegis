output "primary_endpoint" {
  description = "Redis primary endpoint. For single-node, this is cache_nodes[0].address."
  value       = aws_elasticache_cluster.this.cache_nodes[0].address
  sensitive   = true
}

output "port" {
  value = aws_elasticache_cluster.this.cache_nodes[0].port
}

output "cluster_id" {
  value = aws_elasticache_cluster.this.cluster_id
}
