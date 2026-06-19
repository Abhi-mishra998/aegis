variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "bundle_bucket" {
  description = "S3 bucket name where releases/bundle-<sha>.tar.gz live."
  type        = string
}

variable "public_roots_bucket" {
  description = "S3 bucket for the public transparency Merkle roots."
  type        = string
}

variable "secrets_arns" {
  description = "Specific Secrets Manager ARNs the EC2 role may read."
  type        = list(string)
}

variable "ssm_bundle_parameter_arn" {
  description = "ARN of the SSM Parameter holding the active bundle SHA."
  type        = string
}

variable "app_param_arns" {
  description = "ARNs of SSM Parameters the application reads at runtime (Clerk, Stripe, Anthropic, etc.)."
  type        = list(string)
  default     = []
}

variable "audit_kms_key_arn" {
  description = "Audit-envelope KMS CMK ARN. Empty disables the policy."
  type        = string
  default     = ""
}

variable "log_group_arns" {
  description = "CloudWatch log group ARNs the EC2 role writes to."
  type        = list(string)
  default     = []
}
