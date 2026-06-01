# IAM role attached to the EC2 instance. Lets the instance:
#   - read the runtime secrets from Secrets Manager (and ONLY the aegis/* path)
#   - push logs to CloudWatch (instance auto-stop Lambda + agent logs)
# No long-lived AWS keys ever land on the box.

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "agent" {
  name               = "${local.name_prefix}-instance-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

data "aws_iam_policy_document" "agent_runtime" {
  # Pull the agent's provider keys from Secrets Manager
  statement {
    sid    = "ReadAegisSecrets"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${local.name_prefix}/*",
    ]
  }

  # CloudWatch logs (agent journal + lambda)
  statement {
    sid    = "WriteCloudWatchLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "${aws_cloudwatch_log_group.agent.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "agent_runtime" {
  name   = "${local.name_prefix}-runtime"
  role   = aws_iam_role.agent.id
  policy = data.aws_iam_policy_document.agent_runtime.json
}

resource "aws_iam_instance_profile" "agent" {
  name = "${local.name_prefix}-instance-profile"
  role = aws_iam_role.agent.name
}
