# Alias-A records pointing customer hostnames at the ALB. The hosted zone
# itself is NOT managed by this module — it pre-existed and we don't
# want a terraform destroy to delete domain configuration.

resource "aws_route53_record" "alias" {
  for_each = var.alias_records

  zone_id = var.zone_id
  name    = each.key
  type    = "A"

  alias {
    name                   = each.value.target_dns_name
    zone_id                = each.value.target_zone_id
    evaluate_target_health = true
  }
}
