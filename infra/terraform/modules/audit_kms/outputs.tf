output "key_arn" {
  description = "Audit-envelope KMS key ARN."
  value       = aws_kms_key.audit_envelope.arn
}

output "key_id" {
  description = "Audit-envelope KMS key id (UUID)."
  value       = aws_kms_key.audit_envelope.key_id
}

output "alias_name" {
  description = "alias/<name> form — application reads this, not the key id."
  value       = aws_kms_alias.audit_envelope.name
}
