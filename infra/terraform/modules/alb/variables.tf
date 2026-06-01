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
  type    = string
  default = "/health"
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

variable "tags" {
  type    = map(string)
  default = {}
}
