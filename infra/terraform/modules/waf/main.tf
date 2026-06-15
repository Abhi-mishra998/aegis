# Sprint 9 — WAFv2 web ACL for the prod ALB.
#
# Default rule stack (priority order):
#
#   10  AWSManagedRulesCommonRuleSet       — XSS, malformed headers, basic OWASP
#   20  AWSManagedRulesKnownBadInputsRuleSet — log4j etc.
#   30  AWSManagedRulesSQLiRuleSet          — SQL injection signatures
#   100 Per-IP rate limit (optional)
#   200 IP allowlist (optional)
#
# Custom managed rule groups can be added via additional_managed_rules.
# Every rule emits its own CloudWatch metric so the operator can chart
# block rate per category.

resource "aws_wafv2_web_acl" "this" {
  name        = "${var.name_prefix}-web-acl"
  description = "Aegis prod WAFv2 web ACL Sprint 9"
  scope       = "REGIONAL"

  default_action {
    allow {}
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-web-acl"
    sampled_requests_enabled   = true
  }

  rule {
    name     = "AWSManagedRulesCommonRuleSet"
    priority = 10

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
      metric_name                = "${var.name_prefix}-common-rules"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesKnownBadInputsRuleSet"
    priority = 20

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesKnownBadInputsRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-bad-inputs"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "AWSManagedRulesSQLiRuleSet"
    priority = 30

    override_action {
      none {}
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesSQLiRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-sqli"
      sampled_requests_enabled   = true
    }
  }

  dynamic "rule" {
    for_each = var.rate_limit_per_5min > 0 ? [1] : []
    content {
      name     = "${var.name_prefix}-rate-limit"
      priority = 100
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
        metric_name                = "${var.name_prefix}-rate-limit"
        sampled_requests_enabled   = true
      }
    }
  }

  dynamic "rule" {
    for_each = length(var.ip_allowlist_cidrs) > 0 ? [1] : []
    content {
      name     = "${var.name_prefix}-allowlist"
      priority = 200
      action {
        allow {}
      }
      statement {
        ip_set_reference_statement {
          arn = aws_wafv2_ip_set.allowlist[0].arn
        }
      }
      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name_prefix}-allowlist"
        sampled_requests_enabled   = true
      }
    }
  }

  dynamic "rule" {
    for_each = var.additional_managed_rules
    content {
      name     = rule.value.name
      priority = rule.value.priority

      override_action {
        none {}
      }

      statement {
        managed_rule_group_statement {
          name        = rule.value.name
          vendor_name = rule.value.vendor_name
          dynamic "rule_action_override" {
            for_each = rule.value.excluded_rules
            content {
              name = rule_action_override.value
              action_to_use {
                count {}
              }
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "${var.name_prefix}-${rule.value.name}"
        sampled_requests_enabled   = true
      }
    }
  }

  tags = var.tags
}

resource "aws_wafv2_ip_set" "allowlist" {
  count              = length(var.ip_allowlist_cidrs) > 0 ? 1 : 0
  name               = "${var.name_prefix}-allowlist"
  description        = "Sprint 9 — pen-test / vendor allowlist."
  scope              = "REGIONAL"
  ip_address_version = "IPV4"
  addresses          = var.ip_allowlist_cidrs
  tags               = var.tags
}

resource "aws_wafv2_web_acl_association" "this" {
  resource_arn = var.alb_arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}
