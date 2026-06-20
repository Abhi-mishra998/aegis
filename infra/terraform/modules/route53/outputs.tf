output "apex_record_name" {
  description = "Apex A record FQDN."
  value       = aws_route53_record.apex_a.fqdn
}

output "www_record_name" {
  description = "www A record FQDN (ALIAS to ALB)."
  value       = aws_route53_record.www_alias_a.fqdn
}
