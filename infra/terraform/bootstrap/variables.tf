variable "aws_region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Project tag prefix."
  type        = string
  default     = "aegis"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for terraform state."
  type        = string
  default     = "aegis-terraform-state-628478946931"
}
