# Sprint 9 — WAFv2 web ACL attached to the ALB.
#
# Three managed rule groups by default (AWSCommonRuleSet, KnownBadInputs,
# SQLi). The buyer can extend via additional_managed_rules without
# forking the module.

variable "name_prefix" {
  description = "Resource name prefix (e.g. acp-prod-ha)"
  type        = string
}

variable "alb_arn" {
  description = "ALB ARN to attach the web ACL to."
  type        = string
}

variable "additional_managed_rules" {
  description = "Extra AWSManagedRules* groups beyond the three included by default."
  type = list(object({
    name           = string
    priority       = number
    vendor_name    = optional(string, "AWS")
    excluded_rules = optional(list(string), [])
  }))
  default = []
}

variable "ip_allowlist_cidrs" {
  description = "Optional IP allowlist (e.g. tester IPs during pen-test). Empty list disables the rule."
  type        = list(string)
  default     = []
}

variable "rate_limit_per_5min" {
  description = "Per-IP rate limit over 5 minutes; 0 disables the rule."
  type        = number
  default     = 5000
}

variable "tags" {
  description = "Tags merged onto every resource."
  type        = map(string)
  default     = {}
}
