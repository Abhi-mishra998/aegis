variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC id every SG attaches to."
  type        = string
}
