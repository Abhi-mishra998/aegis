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

variable "backup_bucket_suffix" {
  description = <<-EOT
    Suffix appended to the `backups` and `cloudtrail` bucket names. Empty
    for a first-time apply. Set to a suffix like "-v3" during a recovery
    apply when pre-existing buckets under the default name have
    incompatible immutable properties (e.g. `object_lock_enabled = false`)
    that would otherwise force a destructive REPLACE. The old buckets stay
    put and expire via their own lifecycle policies.
  EOT
  type        = string
  default     = ""
}
