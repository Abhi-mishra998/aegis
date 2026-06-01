variable "aws_region" {
  description = "AWS region. ap-south-1 = Mumbai (low latency for the user)."
  type        = string
  default     = "ap-south-1"
}

variable "project_name" {
  description = "Logical project name. Used as prefix on all resources + Secrets Manager paths."
  type        = string
  default     = "aegis-voice-guide"
}

variable "admin_ip_cidr" {
  description = "CIDR block allowed to SSH in. /32 of the user's home IP, NOT 0.0.0.0/0."
  type        = string
  # Detected from api.ipify.org at infra-write time. Override at apply time
  # if the user's IP has changed since.
  default = "103.70.130.212/32"

  validation {
    condition     = can(cidrhost(var.admin_ip_cidr, 0)) && !startswith(var.admin_ip_cidr, "0.0.0.0")
    error_message = "admin_ip_cidr must be a valid CIDR and not 0.0.0.0/anything."
  }
}

variable "instance_type" {
  description = "EC2 instance type. t3.medium is the locked choice per AGENT_V2.md §1.2."
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size_gb" {
  description = "Size of the root EBS volume in GB. Needs to fit Python venv + ChromaDB + BM25 + sentence-transformers + cross-encoder model caches."
  type        = number
  default     = 30
}

variable "billing_alarm_email" {
  description = "Email to receive the CloudWatch billing alarm at $200. Leave empty to skip the SNS subscription (alarm still fires)."
  type        = string
  default     = ""
}

variable "billing_alarm_threshold_usd" {
  description = "Dollar amount at which the billing alarm fires. $200 = early warning before the $250 hard cap."
  type        = number
  default     = 200
}

variable "auto_stop_idle_minutes" {
  description = "Stop the instance if avg CPU < auto_stop_idle_cpu_threshold for this many minutes."
  type        = number
  default     = 30
}

variable "auto_stop_idle_cpu_threshold" {
  description = "CPU % below which the instance is considered idle."
  type        = number
  default     = 5
}
