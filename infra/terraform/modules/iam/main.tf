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

# Instance profile — what EC2 actually receives.
resource "aws_iam_instance_profile" "ec2" {
  name = "${var.name_prefix}-ec2-role"
  role = aws_iam_role.ec2.name
  tags = var.tags
}
