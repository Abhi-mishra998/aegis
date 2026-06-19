output "sns_topic_arn" {
  description = "SNS topic ARN that receives every CloudWatch alarm."
  value       = aws_sns_topic.alarms.arn
}

output "alarm_names" {
  description = "List of alarm names — useful for ops dashboards."
  value = [
    aws_cloudwatch_metric_alarm.alb_5xx_rate.alarm_name,
    aws_cloudwatch_metric_alarm.alb_unhealthy_targets.alarm_name,
    aws_cloudwatch_metric_alarm.rds_free_storage.alarm_name,
  ]
}
