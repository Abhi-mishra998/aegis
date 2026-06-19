# CloudWatch log groups for the application + supporting services.
# Created upfront so the CW agent on EC2 has a writable target on first
# boot — otherwise it logs into a default group with infinite retention.
#
# Retention is bounded; budget visibility matters more than long-tail
# debug here. RDS log group is created automatically by RDS when
# enabled_cloudwatch_logs_exports is set on the instance, so we do NOT
# manage it here.

resource "aws_cloudwatch_log_group" "agent" {
  name              = "/aegis/agent"
  retention_in_days = 14
  skip_destroy      = false

  tags = {
    Name = "${var.name_prefix}-agent-log"
  }
}

resource "aws_cloudwatch_log_group" "gateway" {
  name              = "/aegis/gateway"
  retention_in_days = 14

  tags = {
    Name = "${var.name_prefix}-gateway-log"
  }
}

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/aegis/audit"
  retention_in_days = 30 # audit-layer logs kept longer; still bounded

  tags = {
    Name = "${var.name_prefix}-audit-log"
  }
}
