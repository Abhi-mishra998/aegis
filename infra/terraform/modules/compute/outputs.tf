output "instance_ids" {
  value = aws_instance.app[*].id
}

output "private_ips" {
  value = aws_instance.app[*].private_ip
}

output "public_ips" {
  value = aws_instance.app[*].public_ip
}
