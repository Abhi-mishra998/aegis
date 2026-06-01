variable "name_prefix" {
  type = string
}

variable "instance_count" {
  description = "Number of EC2 instances. Spread across the provided subnets round-robin."
  type        = number
  default     = 1
  validation {
    condition     = var.instance_count >= 1 && var.instance_count <= 10
    error_message = "instance_count must be between 1 and 10 (cost guard)."
  }
}

variable "instance_type" {
  description = "EC2 instance type. dev=t4g.small (ARM Graviton, cheapest); prod=t3.2xlarge (x86) currently."
  type        = string
  default     = "t4g.small"
}

variable "architecture" {
  description = "AMI architecture filter. Must match the instance_type's architecture. x86_64 for t3/t2/m5; arm64 for t4g/m6g."
  type        = string
  default     = "arm64"
  validation {
    condition     = contains(["x86_64", "arm64"], var.architecture)
    error_message = "architecture must be x86_64 or arm64"
  }
}

variable "subnet_ids" {
  description = "Subnet IDs to spread instances across. Public subnets if no NAT, private otherwise."
  type        = list(string)
}

variable "vpc_security_group_ids" {
  type = list(string)
}

variable "iam_instance_profile_name" {
  type = string
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size — application + container images live here"
  type        = number
  default     = 50
}

variable "key_name" {
  description = "EC2 key pair name for SSH. Empty string disables SSH (prefer SSM)."
  type        = string
  default     = ""
}

variable "user_data" {
  description = "Cloud-init userdata. Defaults to running scripts/ops/bootstrap_new_host.sh."
  type        = string
  default     = ""
}

variable "tags" {
  type    = map(string)
  default = {}
}
