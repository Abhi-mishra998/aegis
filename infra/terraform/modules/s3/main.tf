# N S3 buckets configured by a single map. Same hardening on every bucket:
# AES256 SSE, versioning by default, public-access-block on (unless the
# bucket is explicitly public_read = true, which only applies to the
# statuspage bucket).

resource "aws_s3_bucket" "this" {
  for_each      = var.buckets
  bucket        = each.value.bucket_name
  force_destroy = false
  tags          = merge(var.tags, { Name = each.value.bucket_name })
}

resource "aws_s3_bucket_versioning" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  versioning_configuration {
    status = lookup(each.value, "versioning_enabled", true) ? "Enabled" : "Disabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  for_each = var.buckets
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "this" {
  for_each                = var.buckets
  bucket                  = aws_s3_bucket.this[each.key].id
  block_public_acls       = !lookup(each.value, "public_read", false)
  block_public_policy     = !lookup(each.value, "public_read", false)
  ignore_public_acls      = !lookup(each.value, "public_read", false)
  restrict_public_buckets = !lookup(each.value, "public_read", false)
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  for_each = { for k, v in var.buckets : k => v if lookup(v, "expiration_days", 0) > 0 }
  bucket   = aws_s3_bucket.this[each.key].id
  rule {
    id     = "expire-old"
    status = "Enabled"
    filter {} # apply to whole bucket
    expiration {
      days = each.value.expiration_days
    }
    noncurrent_version_expiration {
      noncurrent_days = max(30, floor(each.value.expiration_days / 2))
    }
  }
}

# ALB log delivery — for any bucket marked alb_log_delivery=true, attach
# the well-known AWS-managed bucket policy. Per the regional ELB account
# mapping (see https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html),
# the principal differs per region.

# ap-south-1 ELB log-delivery account id is 718504428378. Other regions
# documented in the official AWS doc; extend this map if you deploy elsewhere.
locals {
  elb_log_delivery_account_id_by_region = {
    "ap-south-1"     = "718504428378"
    "us-east-1"      = "127311923021"
    "us-east-2"      = "033677994240"
    "us-west-1"      = "027434742980"
    "us-west-2"      = "797873946194"
    "eu-west-1"      = "156460612806"
    "eu-central-1"   = "054676820928"
    "ap-southeast-1" = "114774131450"
    "ap-southeast-2" = "783225319266"
    "ap-northeast-1" = "582318560864"
  }
  elb_log_account_id = lookup(local.elb_log_delivery_account_id_by_region, var.aws_region, "718504428378")
}

data "aws_iam_policy_document" "alb_log_delivery" {
  for_each = { for k, v in var.buckets : k => v if lookup(v, "alb_log_delivery", false) }
  statement {
    sid    = "AWSLogDeliveryWrite"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${local.elb_log_account_id}:root"]
    }
    actions = ["s3:PutObject"]
    resources = [
      "${aws_s3_bucket.this[each.key].arn}/*",
    ]
  }
}

resource "aws_s3_bucket_policy" "alb_log_delivery" {
  for_each = { for k, v in var.buckets : k => v if lookup(v, "alb_log_delivery", false) }
  bucket   = aws_s3_bucket.this[each.key].id
  policy   = data.aws_iam_policy_document.alb_log_delivery[each.key].json
}
