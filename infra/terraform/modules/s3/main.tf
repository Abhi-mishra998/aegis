# S3 buckets:
#   - aws_s3_bucket.alb_logs       NEW (ALB access logs, 30-day TTL)
#   - aws_s3_bucket.backups        NEW (Aegis app backups + RDS export targets)
#   - aws_s3_bucket.cloudtrail     NEW (CloudTrail multi-region trail)
#   - aws_s3_bucket.public_roots   IMPORTED (existing, prevent_destroy)
#
# The deploy-bundle bucket (acp-backups-prodha-628478946931) is
# referenced by name only — it predates this stack and EC2 reads it
# via the IAM policy in modules/iam.

# ELB account id for ap-south-1 — needed for ALB log delivery permissions.
data "aws_elb_service_account" "main" {}

# ─── ALB access logs ───────────────────────────────────────────────────
resource "aws_s3_bucket" "alb_logs" {
  bucket        = "${var.name_prefix}-alb-logs-${var.account_id}"
  force_destroy = false

  tags = {
    Name = "${var.name_prefix}-alb-logs"
  }
}

resource "aws_s3_bucket_versioning" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket                  = aws_s3_bucket.alb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    id     = "expire-alb-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.alb_log_retention
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "alb_logs" {
  statement {
    sid     = "AllowELBLogDelivery"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.alb_logs.arn}/alb/AWSLogs/${var.account_id}/*",
    ]
    principals {
      type        = "AWS"
      identifiers = [data.aws_elb_service_account.main.arn]
    }
  }
  statement {
    sid     = "AllowELBLogDeliveryServicePrincipal"
    effect  = "Allow"
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.alb_logs.arn}/alb/AWSLogs/${var.account_id}/*",
    ]
    principals {
      type        = "Service"
      identifiers = ["logdelivery.elasticloadbalancing.amazonaws.com"]
    }
  }
}

resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = data.aws_iam_policy_document.alb_logs.json
}

# ─── Backups bucket ────────────────────────────────────────────────────
resource "aws_s3_bucket" "backups" {
  bucket        = "${var.name_prefix}-backups-${var.account_id}"
  force_destroy = false

  # Sprint EH-5 — S3 Object Lock for tamper-evidence on backup objects.
  # Enabling this on a NEW bucket is free; for the pre-existing
  # production bucket the migration procedure is documented in
  # docs/runbooks/object_lock_migration.md. Once migrated, set
  # `object_lock_enabled = true` on the imported state too.
  object_lock_enabled = true

  tags = {
    Name = "${var.name_prefix}-backups"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    default_retention {
      mode = "GOVERNANCE"   # operator can override with bypass perm; COMPLIANCE locks even root
      days = 30
    }
  }
}

resource "aws_s3_bucket_versioning" "backups" {
  bucket = aws_s3_bucket.backups.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "backups" {
  bucket                  = aws_s3_bucket.backups.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "backups" {
  bucket = aws_s3_bucket.backups.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# ─── CloudTrail bucket ─────────────────────────────────────────────────
# Bucket policy below grants CloudTrail the GetBucketAcl + PutObject
# permissions it needs to ship management-event logs. Without the
# policy, the CloudTrail create call fails with InsufficientS3BucketPolicy.
resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "${var.name_prefix}-cloudtrail-${var.account_id}"
  force_destroy = false

  # Sprint EH-5 — CloudTrail logs are the forensic backstop. Object Lock
  # in COMPLIANCE mode (180 days) means even an admin who steals the
  # root account credentials cannot delete the trail that proves the
  # theft. Migration procedure for the existing bucket in
  # docs/runbooks/object_lock_migration.md.
  object_lock_enabled = true

  tags = {
    Name = "${var.name_prefix}-cloudtrail"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    default_retention {
      mode = "COMPLIANCE"   # cannot be lowered, even by root
      days = 180
    }
  }
}

data "aws_iam_policy_document" "cloudtrail" {
  statement {
    sid       = "AWSCloudTrailAclCheck"
    effect    = "Allow"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.cloudtrail.arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }
  statement {
    sid       = "AWSCloudTrailWrite"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${var.account_id}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = data.aws_iam_policy_document.cloudtrail.json
}

resource "aws_s3_bucket_versioning" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "cloudtrail" {
  bucket                  = aws_s3_bucket.cloudtrail.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ─── Public roots bucket — IMPORTED, never destroyed ───────────────────
# Customer-visible cryptographic archive. The lifecycle block prevents
# `terraform destroy` from touching it. To take it under management:
#
#   terraform import module.s3.aws_s3_bucket.public_roots <bucket-name>
resource "aws_s3_bucket" "public_roots" {
  bucket        = var.public_roots_bucket
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name      = var.public_roots_bucket
    Sensitive = "customer-visible"
  }
}

resource "aws_s3_bucket_versioning" "public_roots" {
  bucket = aws_s3_bucket.public_roots.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "public_roots" {
  bucket = aws_s3_bucket.public_roots.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Public-roots bucket is INTENTIONALLY public-readable for transparency
# verification by any external auditor (verified via `aws s3 ls
# --no-sign-request`). Do not add a public_access_block here — it would
# break the customer-facing transparency model.
# Block on the ACL side only:
resource "aws_s3_bucket_ownership_controls" "public_roots" {
  bucket = aws_s3_bucket.public_roots.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}
