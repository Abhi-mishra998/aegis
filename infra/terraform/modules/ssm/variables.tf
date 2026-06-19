variable "parameter_name" {
  description = "Fully-qualified SSM Parameter name (e.g. /aegis/prod/current_bundle_sha)."
  type        = string
}

variable "initial_value" {
  description = "First SHA seeded into the parameter. Deploy script overwrites later."
  type        = string
}

variable "name_prefix" {
  description = "Project-environment naming prefix (used in tags only)."
  type        = string
}
