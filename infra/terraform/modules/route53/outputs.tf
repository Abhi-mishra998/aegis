output "record_fqdns" {
  value = { for k, r in aws_route53_record.alias : k => r.fqdn }
}
