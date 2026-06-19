output "instance_id" {
  description = "RDS DBInstanceIdentifier."
  value       = aws_db_instance.main.id
}

output "endpoint" {
  description = "Connection endpoint host:port."
  value       = aws_db_instance.main.endpoint
}

output "address" {
  description = "Hostname only (no port)."
  value       = aws_db_instance.main.address
}

output "port" {
  description = "Listening port."
  value       = aws_db_instance.main.port
}

output "resource_id" {
  description = "RDS DbiResourceId (IAM DB auth + audit binding)."
  value       = aws_db_instance.main.resource_id
}

output "arn" {
  description = "RDS instance ARN."
  value       = aws_db_instance.main.arn
}
