variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC id (informational — subnet group + SG carry the actual binding)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet ids for the DB subnet group."
  type        = list(string)
}

variable "rds_security_group" {
  description = "RDS security group id."
  type        = string
}

variable "master_password_secret" {
  description = "Secrets Manager ARN holding the master password."
  type        = string
}

variable "engine_version" {
  description = "Postgres engine version."
  type        = string
  default     = "15.7"
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.small"
}

variable "allocated_storage" {
  description = "Initial gp3 storage GB."
  type        = number
  default     = 50
}

variable "max_allocated_storage" {
  description = "gp3 auto-grow ceiling."
  type        = number
  default     = 200
}

variable "backup_retention" {
  description = "Days RDS retains automated backups."
  type        = number
  default     = 14
}

variable "multi_az" {
  description = "Enable Multi-AZ for in-region failover."
  type        = bool
  default     = true
}
