# One Secrets Manager secret per provider key, namespaced under ${project}/.
# At apply time, values are pulled from sensitive TF variables (set by the
# wrapper scripts from agent/.env.local). The EC2 instance pulls them at
# boot via its IAM role.

# Rationale: these are free-tier provider API keys (Groq/Deepgram/Cartesia/
# LiveKit). They're already encrypted at rest with the AWS-managed key; CMK
# would add ~$1/mo per key × 6 = $6/mo with no real-world security benefit at
# this threat level. If a key leaks the blast radius is "someone burns my free
# tier" — easily mitigated by rotation, not key management ceremony.
# tfsec:ignore:aws-ssm-secret-use-customer-key
resource "aws_secretsmanager_secret" "runtime" {
  for_each = local.runtime_secrets

  name        = "${local.name_prefix}/${each.key}"
  description = "Runtime credential for the Aegis Voice Guide agent."

  # Short window so we don't get blocked re-creating during dev. 7 days is the
  # minimum allowed value.
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "runtime" {
  for_each = local.runtime_secrets

  secret_id     = aws_secretsmanager_secret.runtime[each.key].id
  secret_string = each.value
}

# Optional Gemini fallback — created only if user provides GOOGLE_API_KEY.
resource "aws_secretsmanager_secret" "google_api_key" {
  count                   = var.google_api_key == "" ? 0 : 1
  name                    = "${local.name_prefix}/GOOGLE_API_KEY"
  description             = "Optional Gemini fallback key for LiveKit FallbackAdapter."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "google_api_key" {
  count         = var.google_api_key == "" ? 0 : 1
  secret_id     = aws_secretsmanager_secret.google_api_key[0].id
  secret_string = var.google_api_key
}
