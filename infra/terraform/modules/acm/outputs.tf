output "certificate_arn" {
  value = aws_acm_certificate.this.arn
}

output "validated_certificate_arn" {
  description = "ARN of the validation resource — depend on this to ensure the cert is ISSUED before attaching to a listener."
  value       = aws_acm_certificate_validation.this.certificate_arn
}
