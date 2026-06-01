locals {
  name_prefix = var.project_name

  # Map of provider keys that get pushed into AWS Secrets Manager at apply time.
  # Values come from sensitive Terraform variables (see variables_secrets.tf),
  # populated by scripts/plan.sh / scripts/apply.sh from agent/.env.local.
  runtime_secrets = {
    LIVEKIT_URL        = var.livekit_url
    LIVEKIT_API_KEY    = var.livekit_api_key
    LIVEKIT_API_SECRET = var.livekit_api_secret
    DEEPGRAM_API_KEY   = var.deepgram_api_key
    CARTESIA_API_KEY   = var.cartesia_api_key
    GROQ_API_KEY       = var.groq_api_key
  }
}
