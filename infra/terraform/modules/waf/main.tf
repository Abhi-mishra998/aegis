# WAFv2 Web ACL for the ALB.
#
#  Rule 1 (priority 1): AWSManagedRulesCommonRuleSet
#       — OWASP-aligned baseline (SQLi, XSS, LFI, etc.)
#  Rule 2 (priority 2): AWSManagedRulesBotControlRuleSet (CommonInspection)
#       — known-bot detection. Charged separately ($1/M WCU+req).
#       - 2026-06-22 P2-1: scope-down to UNAUTHENTICATED requests only
#         (request has NO Authorization header). Closes the regression
#         where the managed Bot rule false-positived on Pixel 9 / Chrome
#         149 authenticated traffic to /api/workspace/me. Authenticated
#         traffic has stronger identity signals than Bot Control's
#         user-agent heuristics; we let those requests pass and rely on
#         the gateway's per-tenant auth/quota layers for protection.
#  Rule 3 (priority 5):  UnAuthPerIPRateLimit, 200/5min on UNAUTH paths
#       - 2026-06-22 P2-5: tighten per-IP rate-limit specifically for
#         unauthenticated traffic — closes the denial-of-wallet primitive
#         where 100 anon GETs to /workspace/me sustained 13 req/s for 0
#         429s under the 2000/5min general limit. Scope-down on absence
#         of Authorization header so authenticated callers are not
#         affected by a noisy bot from the same NAT.
#  Rule 4 (priority 10): Per-IP rate limit, 2000/5min default
#       — last-line DDoS / brute-force throttle. Per-IP only — no
#         tenant labels at L7, so a noisy tenant IP behind NAT still
#         throttles correctly.

resource "aws_wafv2_web_acl" "main" {
  name        = "${var.name_prefix}-waf"
  description = "Aegis ALB Web ACL - managed core + bot + per-IP rate limit."
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "AWS-AWSManagedRulesCommonRuleSet"
    priority = 1

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-waf-core"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWS-AWSManagedRulesBotControlRuleSet"
    priority = 2

    # P2-1 (2026-06-22) — keep Block mode (override_action `none {}`
    # lets the managed rule group's own block actions through). Was
    # switched to `count {}` via AWS CLI on 2026-06-22 to unblock a
    # legitimate Pixel 9 / Chrome 149 user. The scope_down below makes
    # that workaround unnecessary: authenticated traffic (Authorization
    # header present) bypasses Bot Control entirely, while anon traffic
    # remains protected.
    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesBotControlRuleSet"
        vendor_name = "AWS"

        managed_rule_group_configs {
          aws_managed_rules_bot_control_rule_set {
            inspection_level = "COMMON"
          }
        }

        # scope_down_statement: only inspect requests that DO NOT carry
        # an Authorization header. Authenticated requests have stronger
        # identity signals (JWT/API key) that the gateway validates;
        # Bot Control's UA/JA3 heuristics false-positive on mobile
        # Chrome and must not be in the path for those requests.
        scope_down_statement {
          not_statement {
            statement {
              size_constraint_statement {
                field_to_match {
                  single_header {
                    name = "authorization"
                  }
                }
                comparison_operator = "GT"
                size                = 0
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-waf-bot"
      sampled_requests_enabled   = true
    }
  }

  rule {
    # P2-5 (2026-06-22) — tighter per-IP cap for unauthenticated traffic.
    # Fires BEFORE the catch-all PerIPRateLimit so anon callers are
    # capped at 200/5min while authenticated callers retain the
    # generous 2000/5min cap via the lower-priority rule (this rule's
    # scope_down excludes them so they pass straight through to
    # priority 10).
    name     = "UnAuthPerIPRateLimit"
    priority = 5

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 200
        aggregate_key_type = "IP"

        scope_down_statement {
          not_statement {
            statement {
              size_constraint_statement {
                field_to_match {
                  single_header {
                    name = "authorization"
                  }
                }
                comparison_operator = "GT"
                size                = 0
                text_transformation {
                  priority = 0
                  type     = "NONE"
                }
              }
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-waf-unauth-ratelimit"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "PerIPRateLimit"
    priority = 10

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = var.rate_limit_per_5min
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-waf-ratelimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-waf"
    sampled_requests_enabled   = true
  }

  tags = {
    Name = "${var.name_prefix}-waf"
  }
}

resource "aws_wafv2_web_acl_association" "alb" {
  resource_arn = var.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.main.arn
}
