output "agent_log_group_arn" {
  description = "Agent log group ARN."
  value       = aws_cloudwatch_log_group.agent.arn
}

output "gateway_log_group_arn" {
  description = "Gateway log group ARN."
  value       = aws_cloudwatch_log_group.gateway.arn
}

output "audit_log_group_arn" {
  description = "Audit log group ARN."
  value       = aws_cloudwatch_log_group.audit.arn
}

output "log_group_arns" {
  description = "All log group ARNs — handed to IAM for the CW agent write policy."
  value = [
    aws_cloudwatch_log_group.agent.arn,
    aws_cloudwatch_log_group.gateway.arn,
    aws_cloudwatch_log_group.audit.arn,
  ]
}
