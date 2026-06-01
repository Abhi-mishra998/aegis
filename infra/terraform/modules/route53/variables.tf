variable "zone_id" {
  description = "Existing hosted zone ID (we do NOT manage zone creation here — the zone pre-existed Aegis)"
  type        = string
}

variable "alias_records" {
  description = "Map of hostname → ALB target (alias A record)"
  type = map(object({
    target_dns_name = string
    target_zone_id  = string
  }))
  default = {}
}

variable "tags" {
  type    = map(string)
  default = {}
}
