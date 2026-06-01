# Auto-stop the EC2 instance when idle. Runs every 5 min via EventBridge.

data "archive_file" "autostop" {
  type        = "zip"
  source_dir  = "${path.module}/lambda/autostop"
  output_path = "${path.module}/lambda/autostop.zip"
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "autostop" {
  name               = "${local.name_prefix}-autostop-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "autostop" {
  statement {
    sid     = "DescribeAndStopThisInstance"
    effect  = "Allow"
    actions = ["ec2:DescribeInstances"]
    # DescribeInstances does not support resource-level permissions.
    # tfsec:ignore:aws-iam-no-policy-wildcards
    resources = ["*"]
  }

  statement {
    sid    = "StopOnlyThisInstance"
    effect = "Allow"
    actions = [
      "ec2:StopInstances",
    ]
    resources = [
      "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${aws_instance.agent.id}",
    ]
  }

  statement {
    sid    = "ReadOurCPUMetric"
    effect = "Allow"
    actions = [
      "cloudwatch:GetMetricStatistics",
    ]
    # CloudWatch GetMetricStatistics does not support resource-level permissions.
    # tfsec:ignore:aws-iam-no-policy-wildcards
    resources = ["*"]
  }

  statement {
    sid    = "LambdaLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-autostop:*",
    ]
  }
}

resource "aws_iam_role_policy" "autostop" {
  name   = "${local.name_prefix}-autostop"
  role   = aws_iam_role.autostop.id
  policy = data.aws_iam_policy_document.autostop.json
}

# Rationale: this log group only captures auto-stop Lambda decisions
# (CPU avg + stopped/noop result). No secrets, no PII. CMK not justified
# at this threat level.
# tfsec:ignore:aws-cloudwatch-log-group-customer-key
resource "aws_cloudwatch_log_group" "autostop" {
  name              = "/aws/lambda/${local.name_prefix}-autostop"
  retention_in_days = 14
}

# Rationale: this Lambda is a single 30-s job that polls CloudWatch and
# optionally stops one instance. The CloudWatch log lines already tell us
# exactly what it did. X-Ray adds cost per trace with no incident-response value.
# tfsec:ignore:aws-lambda-enable-tracing
resource "aws_lambda_function" "autostop" {
  function_name    = "${local.name_prefix}-autostop"
  role             = aws_iam_role.autostop.arn
  runtime          = "python3.12"
  handler          = "index.handler"
  filename         = data.archive_file.autostop.output_path
  source_code_hash = data.archive_file.autostop.output_base64sha256
  timeout          = 30
  memory_size      = 128

  # No VPC: the function only calls AWS APIs over the public internet endpoint,
  # which is reachable via the AWS internal network from the Lambda service.
  # NOTE: reserved_concurrent_executions was removed because fresh AWS accounts
  # have an unreserved-concurrency floor of 10. EventBridge fires one invocation
  # per tick anyway, so a reservation isn't needed.

  environment {
    variables = {
      INSTANCE_ID     = aws_instance.agent.id
      CPU_THRESHOLD   = tostring(var.auto_stop_idle_cpu_threshold)
      IDLE_WINDOW_MIN = tostring(var.auto_stop_idle_minutes)
    }
  }

  depends_on = [aws_cloudwatch_log_group.autostop]
}

resource "aws_cloudwatch_event_rule" "autostop_tick" {
  name                = "${local.name_prefix}-autostop-tick"
  description         = "Tick every 5 minutes to check if the agent instance is idle."
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "autostop_tick" {
  rule      = aws_cloudwatch_event_rule.autostop_tick.name
  target_id = "${local.name_prefix}-autostop"
  arn       = aws_lambda_function.autostop.arn
}

resource "aws_lambda_permission" "autostop_invoke" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.autostop.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.autostop_tick.arn
}
