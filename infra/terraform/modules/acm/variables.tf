variable "domain_name" {
  description = "Primary domain (apex)"
  type        = string
}

variable "subject_alternative_names" {
  description = "Extra hostnames the cert covers"
  type        = list(string)
  default     = []
}

variable "route53_zone_id" {
  description = "Hosted zone where DNS validation records are written"
  type        = string
}

variable "tags" {
  type    = map(string)
  default = {}
}
