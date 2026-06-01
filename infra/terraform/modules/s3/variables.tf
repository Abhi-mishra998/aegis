variable "name_prefix" {
  type = string
}

variable "buckets" {
  description = "Map of logical-name to bucket config. Each gets versioning + SSE + public-access-block."
  type = map(object({
    bucket_name        = string
    versioning_enabled = optional(bool, true)
    expiration_days    = optional(number, 0) # 0 disables lifecycle
    public_read        = optional(bool, false)
    # If true, attaches an AWS-managed bucket policy granting the regional
    # ELB log delivery service permission to write access logs. Required
    # for any bucket used as access_logs_bucket on an ALB.
    alb_log_delivery = optional(bool, false)
  }))
}

variable "aws_region" {
  description = "Region the buckets are created in. Used to look up the ELB log delivery service account id."
  type        = string
  default     = "ap-south-1"
}

variable "tags" {
  type    = map(string)
  default = {}
}
