variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "account_id" {
  description = "AWS account id — used to make bucket names globally unique."
  type        = string
}

variable "alb_log_retention" {
  description = "Days ALB logs are retained before lifecycle expiry."
  type        = number
  default     = 30
}

variable "public_roots_bucket" {
  description = "Existing customer-visible transparency bucket name."
  type        = string
}

variable "bundle_bucket" {
  description = "Existing bundle bucket name (referenced, not created here)."
  type        = string
}
