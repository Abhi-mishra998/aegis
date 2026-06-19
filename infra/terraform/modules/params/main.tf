# SSM Parameters that the application reads at runtime.
#
# All values are marked `ignore_changes = [value]` — Terraform creates
# the parameter as a SecureString placeholder; the operator fills the
# real value via `aws ssm put-parameter --overwrite` after apply.
# This separates infra ownership (parameter exists, IAM grants access)
# from credential ownership (the value comes from Clerk, Stripe, Anthropic,
# etc., and rotates on their schedules — not ours).
#
# To populate after apply:
#
#   aws ssm put-parameter --type SecureString --overwrite \
#     --name /aegis-prodha/clerk/secret-key --value "sk_test_..."
#
# Naming convention: /<env-prefix>/<vendor>/<key-name>

locals {
  # All parameters declared upfront so destroy is symmetric. Each entry:
  #   { name = "/path", description = "...", type = "SecureString" }
  parameters = {
    # Clerk
    "clerk_secret_key"     = { path = "/${var.env_prefix}/clerk/secret-key", description = "Clerk backend secret key." }
    "clerk_publishable"    = { path = "/${var.env_prefix}/clerk/publishable-key", description = "Clerk publishable key (frontend)." }
    "clerk_frontend_api"   = { path = "/${var.env_prefix}/clerk/frontend-api", description = "Clerk frontend API URL." }
    "clerk_jwks_url"       = { path = "/${var.env_prefix}/clerk/jwks-url", description = "Clerk JWKS URL." }
    "clerk_issuer"         = { path = "/${var.env_prefix}/clerk/issuer", description = "Clerk issuer claim." }
    "clerk_jwt_template"   = { path = "/${var.env_prefix}/clerk/jwt-template", description = "Aegis JWT template id." }
    "clerk_webhook_secret" = { path = "/${var.env_prefix}/clerk/webhook-secret", description = "Clerk webhook signing secret." }
    "auth_provider"        = { path = "/${var.env_prefix}/aegis/auth-provider", description = "ACP_AUTH_PROVIDER (legacy|clerk|both)." }

    # Stripe
    "stripe_secret_key"     = { path = "/${var.env_prefix}/stripe/secret-key", description = "Stripe secret key (live or test)." }
    "stripe_webhook_secret" = { path = "/${var.env_prefix}/stripe/webhook-secret", description = "Stripe webhook signing secret." }
    "stripe_pro_price"      = { path = "/${var.env_prefix}/stripe/pro-price-id", description = "Stripe Price ID for the Pro tier." }
    "stripe_ent_price"      = { path = "/${var.env_prefix}/stripe/enterprise-price-id", description = "Stripe Price ID for the Enterprise tier." }

    # Upstream LLM (Path B proxy)
    "anthropic_upstream" = { path = "/${var.env_prefix}/anthropic/upstream-key", description = "Corporate Anthropic API key for /v1/messages proxy." }

    # Container registry + PyPI
    "docker_hub_user" = { path = "/${var.env_prefix}/docker/hub-user", description = "Docker Hub login username." }
    "docker_hub_pat"  = { path = "/${var.env_prefix}/docker/hub-pat", description = "Docker Hub login PAT." }
    "pypi_token"      = { path = "/${var.env_prefix}/pypi/token", description = "PyPI publish token (Track B releases)." }

    # Critical cryptographic key — ed25519 private signing key for the
    # audit Merkle chain. Operator MUST restore from backup post-apply;
    # losing this rotates the public-roots transparency chain to a new
    # signing kid. Existing customer-archived roots remain verifiable
    # against the prior public key (stored alongside in s3://...public-roots/keys/).
    "receipt_signing_key" = { path = "/${var.env_prefix}/receipt-signing-key", description = "Ed25519 private key bytes (base64). PRESERVE across rebuilds — losing it rotates the transparency chain." }
  }
}

resource "aws_ssm_parameter" "this" {
  for_each = local.parameters

  name        = each.value.path
  description = each.value.description
  type        = "SecureString"
  value       = "PLACEHOLDER-overwrite-via-aws-ssm-put-parameter"
  tier        = "Standard"

  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    Name = each.value.path
  }
}
