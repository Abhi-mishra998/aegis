variable "zone_id" {
  description = "Existing Route 53 hosted zone id."
  type        = string
}

variable "domain" {
  description = "Apex domain — must match the hosted zone."
  type        = string
}

variable "alb_dns_name" {
  description = "ALB DNS name (alias target)."
  type        = string
}

variable "alb_zone_id" {
  description = "ALB Route 53 zone id."
  type        = string
}
