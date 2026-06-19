# EC2 instance role + profile. Policies:
#   - AmazonSSMManagedInstanceCore  (Session Manager + SSM agent)
#   - CloudWatchAgentServerPolicy   (push logs)
#   - inline: read bundle bucket + public-roots write + ssm bundle param
#             + Secrets Manager read for db_password + jwt signing
# All scoped tightly — no s3:*, no secretsmanager:*.

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "ec2" {
  name               = "${var.name_prefix}-ec2-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_trust.json

  tags = {
    Name = "${var.name_prefix}-ec2-role"
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy_attachment" "cw_agent" {
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy"
}

# Bundle bucket — EC2 reads release tarballs.
data "aws_iam_policy_document" "bundle_bucket" {
  statement {
    sid       = "ListBundleBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.bundle_bucket}"]
  }
  statement {
    sid       = "ReadBundleObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::${var.bundle_bucket}/releases/*"]
  }
}

resource "aws_iam_policy" "bundle_bucket" {
  name   = "${var.name_prefix}-bundle-read"
  policy = data.aws_iam_policy_document.bundle_bucket.json
}

resource "aws_iam_role_policy_attachment" "bundle_bucket" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.bundle_bucket.arn
}

# Public roots bucket — audit service writes daily Merkle roots.
data "aws_iam_policy_document" "public_roots" {
  statement {
    sid       = "ListPublicRoots"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:aws:s3:::${var.public_roots_bucket}"]
  }
  statement {
    sid       = "WritePublicRoots"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetObject"]
    resources = ["arn:aws:s3:::${var.public_roots_bucket}/*"]
  }
}

resource "aws_iam_policy" "public_roots" {
  name   = "${var.name_prefix}-public-roots-rw"
  policy = data.aws_iam_policy_document.public_roots.json
}

resource "aws_iam_role_policy_attachment" "public_roots" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.public_roots.arn
}

# SSM Parameters — EC2 reads bundle SHA + all app config (Clerk, Stripe, etc.).
data "aws_iam_policy_document" "ssm_param" {
  statement {
    sid     = "ReadBundleSHAParameter"
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
    resources = concat(
      [var.ssm_bundle_parameter_arn],
      var.app_param_arns,
    )
  }
  # Application also needs to decrypt SecureString parameters; the default
  # AWS-managed key for SSM is used (we don't ship a CMK for params).
  statement {
    sid       = "DecryptSSMSecureString"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]
    condition {
      test     = "ForAnyValue:StringEquals"
      variable = "kms:ResourceAliases"
      values   = ["alias/aws/ssm"]
    }
  }
}

resource "aws_iam_policy" "ssm_param" {
  name   = "${var.name_prefix}-ssm-read"
  policy = data.aws_iam_policy_document.ssm_param.json
}

resource "aws_iam_role_policy_attachment" "ssm_param" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.ssm_param.arn
}

# Audit-envelope KMS key — Encrypt/Decrypt/GenerateDataKey on the CMK
# managed by modules/audit_kms.
data "aws_iam_policy_document" "audit_kms" {
  statement {
    sid    = "AuditEnvelopeKeyOps"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    resources = [var.audit_kms_key_arn]
  }
}

resource "aws_iam_policy" "audit_kms" {
  name   = "${var.name_prefix}-audit-kms"
  policy = data.aws_iam_policy_document.audit_kms.json
}

resource "aws_iam_role_policy_attachment" "audit_kms" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.audit_kms.arn
}

# CloudWatch Logs — Write to the managed log groups (CWAgentServerPolicy
# is broader; this scopes writes to our groups only).
data "aws_iam_policy_document" "logs_write" {
  statement {
    sid     = "WriteAegisLogStreams"
    effect  = "Allow"
    actions = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = concat(
      var.log_group_arns,
      [for arn in var.log_group_arns : "${arn}:*"],
    )
  }
}

resource "aws_iam_policy" "logs_write" {
  name   = "${var.name_prefix}-logs-write"
  policy = data.aws_iam_policy_document.logs_write.json
}

resource "aws_iam_role_policy_attachment" "logs_write" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.logs_write.arn
}

# Secrets Manager — only the named secrets.
data "aws_iam_policy_document" "secrets" {
  statement {
    sid       = "ReadAegisSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"]
    resources = var.secrets_arns
  }
}

resource "aws_iam_policy" "secrets" {
  name   = "${var.name_prefix}-secrets-read"
  policy = data.aws_iam_policy_document.secrets.json
}

resource "aws_iam_role_policy_attachment" "secrets" {
  role       = aws_iam_role.ec2.name
  policy_arn = aws_iam_policy.secrets.arn
}

resource "aws_iam_instance_profile" "ec2" {
  name = "${var.name_prefix}-ec2-profile"
  role = aws_iam_role.ec2.name
}
