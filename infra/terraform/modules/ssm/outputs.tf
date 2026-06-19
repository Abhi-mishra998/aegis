output "bundle_parameter_name" {
  description = "SSM Parameter name."
  value       = aws_ssm_parameter.bundle_sha.name
}

output "bundle_parameter_arn" {
  description = "SSM Parameter ARN — granted to the EC2 role for read."
  value       = aws_ssm_parameter.bundle_sha.arn
}
