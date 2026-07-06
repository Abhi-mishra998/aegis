variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "alias_name" {
  description = "KMS alias (sans 'alias/' prefix)."
  type        = string
  default     = "aegis-audit-envelope"
}

variable "ec2_role_arn" {
  description = "ARN of the EC2 IAM role granted Encrypt/Decrypt usage."
  type        = string
}
