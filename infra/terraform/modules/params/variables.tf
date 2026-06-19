variable "env_prefix" {
  description = "Top-level SSM path prefix (e.g. 'aegis-prodha' produces /aegis-prodha/clerk/secret-key)."
  type        = string
}

variable "name_prefix" {
  description = "Project-environment naming prefix (used in tags only)."
  type        = string
}
