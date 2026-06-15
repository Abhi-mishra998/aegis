# Sprint 9 — Autoscaled application tier in private subnets behind an ALB.
#
# Replaces the fixed-instance `compute` module for HA prod: an EC2 launch
# template + Auto Scaling Group spanning every supplied private subnet,
# attached to an ALB target group. The ASG self-heals on instance failure
# and rolls instances when the launch template changes.

variable "name_prefix" {
  description = "Resource name prefix (e.g. acp-prod-ha)"
  type        = string
}

variable "ami_id" {
  description = "AMI id for the launch template — pin per environment via SSM Parameter Store lookup outside this module."
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type (m6g.medium minimum — t4g.* OOMs the 14-container compose stack)"
  type        = string
  default     = "m6g.medium"
}

variable "key_name" {
  description = "EC2 keypair name for SSH (optional in HA prod since SSM is the standard access path)"
  type        = string
  default     = null
}

variable "subnet_ids" {
  description = "PRIVATE subnet ids the ASG launches into. Must span at least 2 AZs."
  type        = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "ASG needs at least 2 subnets across distinct AZs."
  }
}

variable "vpc_security_group_ids" {
  description = "Security groups attached to the launch template's network interface."
  type        = list(string)
}

variable "iam_instance_profile_name" {
  description = "Instance profile granting access to KMS / SSM / Secrets Manager / S3 backups."
  type        = string
}

variable "alb_target_group_arn" {
  description = "Target group ARN the ASG attaches every instance to."
  type        = string
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GB."
  type        = number
  default     = 100
}

variable "root_volume_kms_key_id" {
  description = "Optional KMS CMK for root volume encryption (defaults to the aws-managed CMK)."
  type        = string
  default     = null
}

variable "min_size" {
  description = "Minimum ASG size — also the floor for rolling deploys. Set to 2 for N+1."
  type        = number
  default     = 2
}

variable "desired_capacity" {
  description = "Desired ASG size on steady state."
  type        = number
  default     = 2
}

variable "max_size" {
  description = "Maximum ASG size — the autoscaling ceiling under burst."
  type        = number
  default     = 6
}

variable "health_check_grace_period_seconds" {
  description = "How long after launch the ALB health check is allowed to fail before the ASG kills the instance."
  type        = number
  default     = 300
}

variable "user_data" {
  description = "EC2 user-data script (base64 encoded by the module)."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags merged onto every resource."
  type        = map(string)
  default     = {}
}
