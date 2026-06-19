output "arn" {
  description = "ALB ARN."
  value       = aws_lb.main.arn
}

output "dns_name" {
  description = "ALB DNS name."
  value       = aws_lb.main.dns_name
}

output "zone_id" {
  description = "ALB hosted zone id (for Route 53 ALIAS records)."
  value       = aws_lb.main.zone_id
}

output "arn_suffix" {
  description = "ALB ARN suffix (used by CloudWatch metric dimensions)."
  value       = aws_lb.main.arn_suffix
}

output "target_group_arn" {
  description = "Target group ARN — passed to ASG target_group_arns."
  value       = aws_lb_target_group.main.arn
}

output "target_group_arn_suffix" {
  description = "Target group ARN suffix (CloudWatch metric dimensions)."
  value       = aws_lb_target_group.main.arn_suffix
}

output "target_group_name" {
  description = "Target group name."
  value       = aws_lb_target_group.main.name
}
