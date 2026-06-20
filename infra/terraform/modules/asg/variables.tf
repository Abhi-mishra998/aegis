variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet ids the ASG launches into."
  type        = list(string)
}

variable "ec2_security_group" {
  description = "EC2 security group id."
  type        = string
}

variable "instance_profile" {
  description = "IAM instance profile name (NOT ARN)."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (arm64 — uses Amazon Linux 2023 arm64 AMI)."
  type        = string
  default     = "m6g.large"
}

variable "asg_min" {
  description = "ASG minimum size."
  type        = number
  default     = 2
}

variable "asg_max" {
  description = "ASG maximum size."
  type        = number
  default     = 4
}

variable "asg_desired" {
  description = "ASG desired capacity."
  type        = number
  default     = 2
}

variable "target_group_arn" {
  description = "ALB target group ARN — ASG attaches instances on launch."
  type        = string
}

variable "ssm_bundle_parameter" {
  description = "SSM Parameter name holding the active bundle SHA."
  type        = string
}

variable "bundle_bucket" {
  description = "S3 bucket holding releases/bundle-<sha>.tar.gz."
  type        = string
}

variable "aws_region" {
  description = "AWS region - propagated into user_data for ssm/s3 calls."
  type        = string
}

# ── Runtime config plumbed into the .env on boot ──────────────────────
variable "rds_endpoint" {
  description = "RDS endpoint host:port (used to build DATABASE_URL)."
  type        = string
}

variable "rds_master_secret_id" {
  description = "Secrets Manager secret id holding the RDS master password."
  type        = string
}

variable "redis_primary_endpoint" {
  description = "ElastiCache primary endpoint host:port (TLS rediss://)."
  type        = string
}

variable "domain" {
  description = "Public domain (e.g. aegisagent.in) - used for PUBLIC_BASE_URL."
  type        = string
}

variable "internal_secret_arn" {
  description = "Secrets Manager id for inter-service shared secret."
  type        = string
}

variable "jwt_signing_secret_id" {
  description = "Secrets Manager id for JWT signing key."
  type        = string
}

variable "mesh_jwt_secret_id" {
  description = "Secrets Manager id for mesh JWT secret."
  type        = string
}

variable "stripe_webhook_secret_id" {
  description = "Secrets Manager id for Stripe webhook secret."
  type        = string
}

variable "groq_api_key_secret_id" {
  description = "Secrets Manager id for Groq API key."
  type        = string
}

variable "app_param_prefix" {
  description = "SSM parameter prefix for app config (no leading slash)."
  type        = string
}

variable "public_roots_bucket" {
  description = "Public transparency roots bucket."
  type        = string
}
