variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "alarm_email" {
  description = "Email address that receives the SNS notifications."
  type        = string
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix (CloudWatch dimension)."
  type        = string
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix (CloudWatch dimension)."
  type        = string
}

variable "rds_instance_identifier" {
  description = "RDS DBInstanceIdentifier (CloudWatch dimension)."
  type        = string
}
