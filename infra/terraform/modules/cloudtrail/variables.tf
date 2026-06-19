variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "cloudtrail_bucket_name" {
  description = "Name of the S3 bucket CloudTrail writes to. Must have the CloudTrail service principal write permission set on its bucket policy."
  type        = string
}
