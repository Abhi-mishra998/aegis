output "instance_id" {
  description = "The EC2 instance ID. Used by scripts/{start,stop,ssh}.sh."
  value       = aws_instance.agent.id
}

output "public_ip" {
  description = "Elastic IP attached to the instance. Stable across stop/start."
  value       = aws_eip.agent.public_ip
}

output "ssh_command" {
  description = "Copy-pasteable SSH command. PEM is written to infrastructure/aegis-voice-guide.pem."
  value       = "ssh -i ${path.module}/${var.project_name}.pem ubuntu@${aws_eip.agent.public_ip}"
}

output "pem_path" {
  description = "Path to the private key file (0400). Gitignored."
  value       = local_sensitive_file.private_key_pem.filename
}

output "region" {
  value = var.aws_region
}

output "log_group" {
  description = "CloudWatch log group for the agent."
  value       = aws_cloudwatch_log_group.agent.name
}

output "secret_arns" {
  description = "ARNs of the runtime secrets the instance reads at boot."
  value       = [for s in aws_secretsmanager_secret.runtime : s.arn]
}
