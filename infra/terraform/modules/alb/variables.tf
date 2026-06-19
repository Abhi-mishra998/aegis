variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet ids the ALB attaches to (exactly 2 required)."
  type        = list(string)
}

variable "alb_security_group" {
  description = "Security group id for the ALB."
  type        = string
}

variable "acm_certificate_arn" {
  description = "ACM certificate ARN for the HTTPS listener."
  type        = string
}

variable "alb_log_bucket" {
  description = "S3 bucket name where ALB access logs ship."
  type        = string
}

variable "log_retention_days" {
  description = "Retention applied via lifecycle on the S3 bucket; bucket itself owned by s3 module."
  type        = number
  default     = 30
}
