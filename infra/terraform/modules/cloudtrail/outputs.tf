output "trail_arn" {
  description = "CloudTrail trail ARN."
  value       = aws_cloudtrail.mgmt_events.arn
}

output "trail_name" {
  description = "CloudTrail trail name."
  value       = aws_cloudtrail.mgmt_events.name
}
