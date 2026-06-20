# Apex A + AAAA + www CNAME against the ALB. The zone itself is NOT
# created here — it pre-existed at zone_id (passed in from data source
# at the root).
#
# Apex A and AAAA are ALIAS records so they resolve to the ALB without
# a CNAME lookup. The ALIAS pattern is free and faster than CNAME for
# the apex.

resource "aws_route53_record" "apex_a" {
  zone_id         = var.zone_id
  name            = var.domain
  type            = "A"
  allow_overwrite = true

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "apex_aaaa" {
  zone_id         = var.zone_id
  name            = var.domain
  type            = "AAAA"
  allow_overwrite = true

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}

resource "aws_route53_record" "www_alias_a" {
  zone_id         = var.zone_id
  name            = "www.${var.domain}"
  type            = "A"
  allow_overwrite = true

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}
