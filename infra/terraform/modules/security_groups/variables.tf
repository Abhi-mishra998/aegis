variable "name_prefix" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "ssh_allowed_cidrs" {
  description = "Source CIDRs allowed to SSH (port 22). [] disables SSH ingress entirely (preferred)."
  type        = list(string)
  default     = []
}

variable "gateway_port" {
  description = "Gateway HTTP port — ALB forwards here"
  type        = number
  default     = 8000
}

variable "ui_port" {
  description = "UI nginx HTTP port — also exposed to ALB"
  type        = number
  default     = 5173
}

variable "tags" {
  type    = map(string)
  default = {}
}
