output "alb_logs_bucket" {
  description = "ALB access logs bucket name (passed to ALB access_logs config)."
  value       = aws_s3_bucket.alb_logs.id
}

output "alb_logs_bucket_arn" {
  description = "ALB logs bucket ARN."
  value       = aws_s3_bucket.alb_logs.arn
}

output "backups_bucket" {
  description = "Aegis backups bucket name."
  value       = aws_s3_bucket.backups.id
}

output "cloudtrail_bucket" {
  description = "CloudTrail logs bucket name."
  value       = aws_s3_bucket.cloudtrail.id
}

output "public_roots_bucket" {
  description = "Public roots bucket name."
  value       = aws_s3_bucket.public_roots.id
}
