variable "name_prefix" {
  type = string
}

variable "s3_backup_bucket_arns" {
  description = "ARNs of S3 buckets the EC2 host writes backups to. Read+write granted via inline policy."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}
