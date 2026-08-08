# Customer-managed KMS key for audit-receipt envelope encryption.
#
# The Aegis audit pipeline wraps each receipt's ed25519 signature in an
# envelope encrypted with this CMK. Customer-managed (not aws-managed)
# so the operator can rotate, audit usage via CloudTrail KMS events,
# and disable the key as an immediate kill-switch on suspected compromise.
#
# Alias is stable across rotations; key id changes if the key is
# regenerated. Application code should always read `alias/aegis-audit-envelope`
# rather than pinning the key id.

resource "aws_kms_key" "audit_envelope" {
  description              = "Aegis audit-receipt envelope encryption (CMK)."
  key_usage                = "ENCRYPT_DECRYPT"
  customer_master_key_spec = "SYMMETRIC_DEFAULT"
  enable_key_rotation      = true
  deletion_window_in_days  = 30 # max protect window — rotation is the safer knob

  policy = data.aws_iam_policy_document.audit_envelope.json

  tags = {
    Name = "${var.name_prefix}-audit-envelope"
  }
}

resource "aws_kms_alias" "audit_envelope" {
  name          = "alias/${var.alias_name}"
  target_key_id = aws_kms_key.audit_envelope.key_id
}

# Split root policy (fix M3 per 31.anaysis.md):
#   1. Root gets key-policy MANAGEMENT only (PutKeyPolicy, alias ops,
#      Describe/List, rotation toggles, Tag ops). Root can NOT Encrypt,
#      Decrypt, GenerateDataKey, ReEncrypt, or ScheduleKeyDeletion.
#   2. Explicit Deny on the crypto/lifecycle ops for every principal
#      OTHER than the audit-writer role. NotPrincipal + Deny beats any
#      allow-elsewhere; the previous `kms:*` on root defeated the
#      immutable-evidence story because any admin could grant themselves
#      Decrypt via PutKeyPolicy and read historic audit envelopes.
#   3. Audit-writer role keeps its narrow Encrypt/Decrypt/GenerateDataKey
#      grant for envelope wrapping.
data "aws_iam_policy_document" "audit_envelope" {
  statement {
    sid    = "EnableIAMUserPermissions"
    effect = "Allow"
    actions = [
      "kms:PutKeyPolicy",
      "kms:GetKeyPolicy",
      "kms:UpdateAlias",
      "kms:CreateAlias",
      "kms:DescribeKey",
      "kms:ListKeys",
      "kms:ListAliases",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:GetKeyRotationStatus",
      "kms:EnableKeyRotation",
      "kms:DisableKey",
      "kms:EnableKey",
    ]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    resources = ["*"]
  }

  # Explicit-Deny wall on the crypto + lifecycle ops for everyone
  # EXCEPT the audit-writer role. Overrides any future IAM-level grant.
  statement {
    sid    = "DenyCryptoOpsToNonAuditWriter"
    effect = "Deny"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:GenerateDataKey*",
      "kms:ReEncrypt*",
      "kms:ScheduleKeyDeletion",
    ]
    not_principals {
      type        = "AWS"
      identifiers = [var.audit_writer_role_arn]
    }
    resources = ["*"]
  }

  statement {
    sid    = "AllowEC2RoleUseOfKey"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:DescribeKey",
    ]
    principals {
      type        = "AWS"
      identifiers = [var.ec2_role_arn]
    }
    resources = ["*"]
  }
}

data "aws_caller_identity" "current" {}
