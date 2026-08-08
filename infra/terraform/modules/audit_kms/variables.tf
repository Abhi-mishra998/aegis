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

variable "audit_writer_role_arn" {
  description = "ARN of the role permitted to Encrypt/Decrypt/GenerateDataKey* on this CMK. Used by the NotPrincipal wall (M3 fix). Today this is the EC2 role — set to a dedicated audit-writer role once services are split."
  type        = string
}
