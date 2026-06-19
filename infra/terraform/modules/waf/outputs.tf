output "web_acl_arn" {
  description = "WAFv2 Web ACL ARN."
  value       = aws_wafv2_web_acl.main.arn
}

output "web_acl_id" {
  description = "WAFv2 Web ACL id."
  value       = aws_wafv2_web_acl.main.id
}
