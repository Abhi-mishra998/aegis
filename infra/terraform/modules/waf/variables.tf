variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "rate_limit_per_5min" {
  description = "WAF rate-limit threshold per source IP over a 5-minute window."
  type        = number
  default     = 2000
}

variable "alb_arn" {
  description = "ALB ARN the Web ACL associates to."
  type        = string
}
