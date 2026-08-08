output "db_password_arn" {
  description = "Secrets Manager ARN for the DB master password."
  value       = aws_secretsmanager_secret.db_password.arn
}

output "db_password_name" {
  description = "Secrets Manager name (without ARN)."
  value       = aws_secretsmanager_secret.db_password.name
}

output "jwt_signing_arn" {
  description = "Secrets Manager ARN for the JWT signing key."
  value       = aws_secretsmanager_secret.jwt_signing.arn
}

output "jwt_signing_name" {
  description = "Secrets Manager name for the JWT signing key."
  value       = aws_secretsmanager_secret.jwt_signing.name
}

output "internal_secret_arn" {
  description = "Internal-service shared secret ARN."
  value       = aws_secretsmanager_secret.internal_secret.arn
}

output "redis_auth_token_arn" {
  description = "Redis AUTH token ARN."
  value       = aws_secretsmanager_secret.redis_auth_token.arn
}

output "redis_auth_token_value" {
  description = "Redis AUTH token plaintext (fix M6). Wired into module.elasticache so the token is set at cluster-create time. Sensitive — never expose in logs."
  value       = random_password.redis_auth_token.result
  sensitive   = true
}

output "mesh_jwt_secret_arn" {
  description = "Mesh JWT secret ARN."
  value       = aws_secretsmanager_secret.mesh_jwt_secret.arn
}

output "groq_api_key_arn" {
  description = "Groq API key ARN (operator-supplied value)."
  value       = aws_secretsmanager_secret.groq_api_key.arn
}

output "all_secret_arns" {
  description = "Every secret ARN — passed to the IAM module so the EC2 role can read them all."
  value = [
    aws_secretsmanager_secret.db_password.arn,
    aws_secretsmanager_secret.jwt_signing.arn,
    aws_secretsmanager_secret.internal_secret.arn,
    aws_secretsmanager_secret.redis_auth_token.arn,
    aws_secretsmanager_secret.mesh_jwt_secret.arn,
    aws_secretsmanager_secret.groq_api_key.arn,
  ]
}
