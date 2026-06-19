output "name" {
  description = "Auto Scaling Group name."
  value       = aws_autoscaling_group.main.name
}

output "arn" {
  description = "Auto Scaling Group ARN."
  value       = aws_autoscaling_group.main.arn
}

output "launch_template_id" {
  description = "Launch Template id."
  value       = aws_launch_template.main.id
}

output "launch_template_latest_version" {
  description = "Launch Template latest version number."
  value       = aws_launch_template.main.latest_version
}
