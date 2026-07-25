# Two random_password-backed secrets. Operator never sees the value;
# EC2 reads via instance profile at runtime.
#
# To rotate: `terraform taint random_password.<name>; terraform apply`.
# The Secrets Manager version rotates atomically; EC2 must re-read on
# next request (the SDK caches but expires within a minute).

resource "random_password" "db_master" {
  length           = 24
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_secretsmanager_secret" "db_password" {
  name        = "${var.name_prefix}-db-master-password"
  description = "Postgres master password for the RDS instance."

  recovery_window_in_days = 7

  tags = {
    Name = "${var.name_prefix}-db-master-password"
  }
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db_master.result
}

resource "random_password" "jwt_signing" {
  length  = 64
  special = false # base64-safe; consumers expect alphanumeric

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_secretsmanager_secret" "jwt_signing" {
  name        = "${var.name_prefix}-jwt-signing-key"
  description = "Aegis JWT HS256 signing key (legacy path)."

  recovery_window_in_days = 7

  tags = {
    Name = "${var.name_prefix}-jwt-signing-key"
  }
}

resource "aws_secretsmanager_secret_version" "jwt_signing" {
  secret_id     = aws_secretsmanager_secret.jwt_signing.id
  secret_string = random_password.jwt_signing.result
}

# ─── Auto-generated secrets ────────────────────────────────────────────
# Three additional secrets that Aegis services need but that the operator
# does NOT supply — Terraform generates and stores them.

resource "random_password" "internal_secret" {
  length  = 64
  special = false
  lifecycle { create_before_destroy = true }
}

resource "aws_secretsmanager_secret" "internal_secret" {
  name                    = "${var.name_prefix}-internal-secret"
  description             = "Inter-service shared secret (X-Internal-Secret header)."
  recovery_window_in_days = 7
  tags                    = { Name = "${var.name_prefix}-internal-secret" }
}

resource "aws_secretsmanager_secret_version" "internal_secret" {
  secret_id     = aws_secretsmanager_secret.internal_secret.id
  secret_string = random_password.internal_secret.result
}

resource "random_password" "redis_auth_token" {
  length  = 32
  special = false
  lifecycle { create_before_destroy = true }
}

resource "aws_secretsmanager_secret" "redis_auth_token" {
  name                    = "${var.name_prefix}-redis-auth-token"
  description             = "Redis AUTH token (used only if ElastiCache auth_token is enabled)."
  recovery_window_in_days = 7
  tags                    = { Name = "${var.name_prefix}-redis-auth-token" }
}

resource "aws_secretsmanager_secret_version" "redis_auth_token" {
  secret_id     = aws_secretsmanager_secret.redis_auth_token.id
  secret_string = random_password.redis_auth_token.result
}

resource "random_password" "mesh_jwt_secret" {
  length  = 64
  special = false
  lifecycle { create_before_destroy = true }
}

resource "aws_secretsmanager_secret" "mesh_jwt_secret" {
  name                    = "${var.name_prefix}-mesh-jwt-secret"
  description             = "Service-mesh JWT signing key for inter-service calls."
  recovery_window_in_days = 7
  tags                    = { Name = "${var.name_prefix}-mesh-jwt-secret" }
}

resource "aws_secretsmanager_secret_version" "mesh_jwt_secret" {
  secret_id     = aws_secretsmanager_secret.mesh_jwt_secret.id
  secret_string = random_password.mesh_jwt_secret.result
}

# ─── Operator-supplied secrets (created empty; ignore value on read) ───
# Created so IAM grants are wired and the names are stable; operator
# fills the value via `aws secretsmanager put-secret-value` post-apply.

resource "aws_secretsmanager_secret" "groq_api_key" {
  name                    = "${var.name_prefix}-groq-api-key"
  description             = "Groq API key (voice agent). Set value via aws secretsmanager put-secret-value."
  recovery_window_in_days = 7
  tags                    = { Name = "${var.name_prefix}-groq-api-key" }
}

resource "aws_secretsmanager_secret_version" "groq_api_key" {
  secret_id     = aws_secretsmanager_secret.groq_api_key.id
  secret_string = "PLACEHOLDER-overwrite-via-aws-secretsmanager-put-secret-value"

  lifecycle { ignore_changes = [secret_string] }
}
