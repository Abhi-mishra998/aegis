# Sensitive variables — populated at runtime by scripts/plan.sh and
# scripts/apply.sh from agent/.env.local. NEVER hard-code values here.

variable "livekit_url" {
  type      = string
  sensitive = true
}

variable "livekit_api_key" {
  type      = string
  sensitive = true
}

variable "livekit_api_secret" {
  type      = string
  sensitive = true
}

variable "deepgram_api_key" {
  type      = string
  sensitive = true
}

variable "cartesia_api_key" {
  type      = string
  sensitive = true
}

variable "groq_api_key" {
  type      = string
  sensitive = true
}

# Optional: Gemini fallback. Empty string is fine — the agent will skip the
# fallback if the key is empty / absent.
variable "google_api_key" {
  type      = string
  sensitive = true
  default   = ""
}
