output "state_bucket_name" {
  description = "The state bucket name — paste into backend.tf bucket field."
  value       = aws_s3_bucket.tf_state.id
}

output "state_bucket_arn" {
  description = "ARN of the state bucket."
  value       = aws_s3_bucket.tf_state.arn
}
