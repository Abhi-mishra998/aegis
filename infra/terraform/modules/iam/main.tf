# EC2 instance role with the minimum permissions Aegis needs:
#   - SSM Session Manager access (replaces SSH)
#   - CloudWatch Agent write access for logs + custom metrics
#   - Scoped S3 read/write for the backup buckets only (NOT *FullAccess)
#
# This module intentionally does NOT attach `AmazonS3FullAccess` even
# though the live prod role has it — that attachment is a sprint-8.5
# follow-up to scope down. See environments/prod/IMPORT.md.

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.name_prefix}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.assume_ec2.json
  tags               = merge(var.tags, { Name = "${var.name_prefix}-ec2-role" })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cloudwatch_agent" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# Scoped S3 access — only the listed backup buckets, not *FullAccess.
data "aws_iam_policy_document" "s3_backup" {
  count = length(var.s3_backup_bucket_arns) > 0 ? 1 : 0

  statement {
    sid    = "ListBackupBuckets"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = var.s3_backup_bucket_arns
  }

  statement {
    sid    = "ReadWriteBackupObjects"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:GetObjectVersion",
    ]
    resources = [for arn in var.s3_backup_bucket_arns : "${arn}/*"]
  }
}

resource "aws_iam_role_policy" "s3_backup" {
  count  = length(var.s3_backup_bucket_arns) > 0 ? 1 : 0
  name   = "${var.name_prefix}-s3-backup"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.s3_backup[0].json
}

# Sprint 9 — bundle-launch reads: RDS endpoint, Redis endpoint, Secrets,
# SSM parameters. Scoped to the prod-ha resources by name prefix.
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "bundle_launch" {
  statement {
    sid    = "DescribeInfra"
    effect = "Allow"
    actions = [
      "rds:DescribeDBInstances",
      "elasticache:DescribeReplicationGroups",
      "elasticache:DescribeCacheClusters",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ReadSecretsManager"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      "arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}/*",
    ]
  }

  statement {
    sid    = "ReadSSMParameters"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [
      "arn:aws:ssm:*:${data.aws_caller_identity.current.account_id}:parameter/${var.name_prefix}/*",
    ]
  }

  statement {
    sid       = "KmsDecryptForSecretsAndSsm"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:aws:secretsmanager:arn"
      values   = ["arn:aws:secretsmanager:*:${data.aws_caller_identity.current.account_id}:secret:${var.name_prefix}/*"]
    }
  }
}

resource "aws_iam_role_policy" "bundle_launch" {
  name   = "${var.name_prefix}-bundle-launch"
  role   = aws_iam_role.ec2.id
  policy = data.aws_iam_policy_document.bundle_launch.json
}

# Instance profile — what EC2 actually receives.
resource "aws_iam_instance_profile" "ec2" {
  name = "${var.name_prefix}-ec2-role"
  role = aws_iam_role.ec2.name
  tags = var.tags
}
