output "endpoint" {
  value     = aws_db_instance.this.endpoint
  sensitive = true
}

output "address" {
  value     = aws_db_instance.this.address
  sensitive = true
}

output "port" {
  value = aws_db_instance.this.port
}

output "identifier" {
  value = aws_db_instance.this.identifier
}

output "db_name" {
  value = aws_db_instance.this.db_name
}
