# CloudWatch billing alarm + log group for the agent + SNS topic for alerts.
# Billing metrics live ONLY in us-east-1 — note the aliased provider below.

# Rationale: agent logs are operational journals (no PII, no secrets — secrets
# live in Secrets Manager). CMK ($1+/mo per key + usage) is not justified
# against a $30/mo portfolio budget.
# tfsec:ignore:aws-cloudwatch-log-group-customer-key
resource "aws_cloudwatch_log_group" "agent" {
  name              = "/aegis/agent"
  retention_in_days = 14
}

# Billing metrics are emitted only in us-east-1, regardless of where resources
# actually live. Use a regional provider alias to read them from there.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = "aegis-voice-guide"
      Environment = "portfolio"
      ManagedBy   = "terraform"
      Owner       = "abhishek"
    }
  }
}

# Rationale: this topic only carries "your AWS bill crossed $200" notifications.
# Not sensitive data. AWS-managed SSE is sufficient; CMK adds ~$1/mo with no
# benefit at this threat level.
# tfsec:ignore:aws-sns-topic-encryption-use-cmk
resource "aws_sns_topic" "billing_alerts" {
  provider = aws.us_east_1
  name     = "${local.name_prefix}-billing-alerts"

  kms_master_key_id = "alias/aws/sns"
}

resource "aws_sns_topic_subscription" "billing_email" {
  count = var.billing_alarm_email == "" ? 0 : 1

  provider  = aws.us_east_1
  topic_arn = aws_sns_topic.billing_alerts.arn
  protocol  = "email"
  endpoint  = var.billing_alarm_email
}

resource "aws_cloudwatch_metric_alarm" "billing" {
  provider = aws.us_east_1

  alarm_name          = "${local.name_prefix}-billing-${var.billing_alarm_threshold_usd}usd"
  alarm_description   = "Estimated monthly AWS charges crossed $${var.billing_alarm_threshold_usd}. Hard cap target = $250."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  metric_name         = "EstimatedCharges"
  namespace           = "AWS/Billing"
  period              = 21600 # 6 h
  statistic           = "Maximum"
  threshold           = var.billing_alarm_threshold_usd
  treat_missing_data  = "notBreaching"

  dimensions = {
    Currency = "USD"
  }

  alarm_actions = [aws_sns_topic.billing_alerts.arn]
}
