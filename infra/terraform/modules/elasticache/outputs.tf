output "replication_group_id" {
  description = "Replication group id."
  value       = aws_elasticache_replication_group.main.replication_group_id
}

output "primary_endpoint" {
  description = "Primary endpoint host:port (read+write)."
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "reader_endpoint" {
  description = "Reader endpoint (read-only across replicas)."
  value       = aws_elasticache_replication_group.main.reader_endpoint_address
}

output "port" {
  description = "Redis port."
  value       = aws_elasticache_replication_group.main.port
}
