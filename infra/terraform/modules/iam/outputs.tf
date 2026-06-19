output "instance_profile_name" {
  description = "Instance profile name passed to the Launch Template."
  value       = aws_iam_instance_profile.ec2.name
}

output "instance_profile_arn" {
  description = "Instance profile ARN."
  value       = aws_iam_instance_profile.ec2.arn
}

output "ec2_role_arn" {
  description = "EC2 IAM role ARN."
  value       = aws_iam_role.ec2.arn
}
