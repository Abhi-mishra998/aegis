variable "name_prefix" {
  type = string
}

variable "identifier" {
  description = "RDS instance identifier (override per environment to keep stable across recreates)"
  type        = string
}

variable "engine_version" {
  description = "Postgres version. Pin minor so minor-upgrades are deliberate."
  type        = string
  default     = "15.18"
}

variable "instance_class" {
  description = "dev=db.t4g.micro (cheapest), prod=db.t3.micro (matches live state)."
  type        = string
  default     = "db.t4g.micro"
}

variable "allocated_storage_gb" {
  type    = number
  default = 20
}

variable "max_allocated_storage_gb" {
  description = "RDS storage auto-scaling ceiling"
  type        = number
  default     = 100
}

variable "multi_az" {
  description = "Multi-AZ doubles the cost. dev=false, prod=true."
  type        = bool
  default     = false
}

variable "subnet_ids" {
  description = "Private subnet IDs across at least 2 AZs"
  type        = list(string)
}

variable "vpc_security_group_ids" {
  type = list(string)
}

variable "master_username" {
  type    = string
  default = "postgres"
}

variable "master_password_secret_arn" {
  description = "Secrets Manager ARN holding the master password (plaintext string secret)"
  type        = string
}

variable "db_name" {
  type    = string
  default = "acp"
}

variable "backup_retention_period_days" {
  type    = number
  default = 7
}

variable "deletion_protection" {
  description = "prod=true, dev=false so iteration is cheap"
  type        = bool
  default     = false
}

variable "enhanced_monitoring_interval_seconds" {
  description = "Enhanced Monitoring interval. 0 disables (and skips the IAM role). 60 enables 1-minute granularity but requires monitoring_role_arn."
  type        = number
  default     = 0
}

variable "monitoring_role_arn" {
  description = "IAM role for Enhanced Monitoring. Required if enhanced_monitoring_interval_seconds > 0."
  type        = string
  default     = ""
}

variable "skip_final_snapshot" {
  description = "prod=false, dev=true"
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
