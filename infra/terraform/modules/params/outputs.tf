output "parameter_arns" {
  description = "List of every parameter ARN created — IAM module grants ssm:GetParameter on these."
  value       = [for p in aws_ssm_parameter.this : p.arn]
}

output "parameter_names" {
  description = "Map of logical name -> parameter path."
  value       = { for k, p in aws_ssm_parameter.this : k => p.name }
}

output "parameter_count" {
  description = "Count of managed parameters."
  value       = length(aws_ssm_parameter.this)
}
