variable "name_prefix" {
  type = string
}

variable "alb_name" {
  description = "ALB resource name (matches the live ID for prod imports)"
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Public subnets the ALB listens on (>= 2 across different AZs)"
  type        = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "target_port" {
  description = "Port the EC2 hosts listen on. The current live ALB forwards to port 5173 (UI nginx, which reverse-proxies the gateway)."
  type        = number
  default     = 5173
}

variable "health_check_path" {
  description = <<-EOT
    Target-group health-check path.

    U13: must be `/healthz`, which the UI nginx proxies to gateway:8000/health
    with a tight 2s timeout. The previous default `/health` was a STATIC nginx
    200, so a dead gateway behind a healthy nginx stayed registered and the
    ALB kept sending live traffic to a dead backend.
  EOT
  type        = string
  default     = "/healthz"
}

variable "certificate_arn" {
  description = "ACM cert ARN for the HTTPS listener"
  type        = string
}

variable "target_instance_ids" {
  description = "EC2 instance IDs to register with the target group"
  type        = list(string)
  default     = []
}

variable "access_logs_bucket" {
  description = "S3 bucket for ALB access logs. Empty disables logging."
  type        = string
  default     = ""
}

# Sprint 9 — deploy-bug-fix #1.
#
# The original module hardcoded `enable_deletion_protection = true`, which
# meant `terraform destroy` hung on the non-prod environments. Defaulting
# to true preserves the prod-safe behaviour; dev (and other ephemeral
# environments) set this to false so destroy/apply cycles work cleanly.
variable "enable_deletion_protection" {
  description = "ALB delete-protection. KEEP TRUE in prod; set false for ephemeral envs that get destroyed often."
  type        = bool
  default     = true
}

variable "tags" {
  type    = map(string)
  default = {}
}
