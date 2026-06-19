# Three alarms + SNS topic + email subscription.
# In-platform alarms (chain violation, gateway p99) already live in
# Alertmanager; CloudWatch is reserved for AWS-layer signals only.

resource "aws_sns_topic" "alarms" {
  name = "${var.name_prefix}-alarms"

  # Server-side encryption with the AWS-managed key for SNS. Free; ties
  # message-at-rest encryption to the regional KMS. Upgrade to CMK only
  # if an F500 demands BYOK.
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Name = "${var.name_prefix}-alarms"
  }
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

# Alarm 1 — ALB 5xx rate > 1% over 5 min.
resource "aws_cloudwatch_metric_alarm" "alb_5xx_rate" {
  alarm_name          = "${var.name_prefix}-alb-5xx-rate"
  alarm_description   = "ALB 5xx rate > 1% over 5 minutes."
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "errorRate"
    expression  = "100 * (e1 / IF(e2 == 0, 1, e2))"
    label       = "ALB 5xx rate (%)"
    return_data = true
  }

  metric_query {
    id = "e1"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "HTTPCode_ELB_5XX_Count"
      period      = 300
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  metric_query {
    id = "e2"
    metric {
      namespace   = "AWS/ApplicationELB"
      metric_name = "RequestCount"
      period      = 300
      stat        = "Sum"
      dimensions = {
        LoadBalancer = var.alb_arn_suffix
      }
    }
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

# Alarm 2 — at least one healthy target.
resource "aws_cloudwatch_metric_alarm" "alb_unhealthy_targets" {
  alarm_name          = "${var.name_prefix}-alb-no-healthy-targets"
  alarm_description   = "Fewer than 1 healthy target for 2 minutes."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  period              = 60
  threshold           = 1
  treat_missing_data  = "breaching"

  namespace   = "AWS/ApplicationELB"
  metric_name = "HealthyHostCount"
  statistic   = "Minimum"

  dimensions = {
    LoadBalancer = var.alb_arn_suffix
    TargetGroup  = var.target_group_arn_suffix
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}

# Alarm 3 — RDS free storage < 10 GiB.
resource "aws_cloudwatch_metric_alarm" "rds_free_storage" {
  alarm_name          = "${var.name_prefix}-rds-free-storage-low"
  alarm_description   = "RDS free storage below 10 GiB."
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 2
  period              = 300
  threshold           = 10737418240 # 10 GiB
  treat_missing_data  = "breaching"

  namespace   = "AWS/RDS"
  metric_name = "FreeStorageSpace"
  statistic   = "Minimum"

  dimensions = {
    DBInstanceIdentifier = var.rds_instance_identifier
  }

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn]
}
