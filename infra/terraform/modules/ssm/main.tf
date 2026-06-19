# SSM Parameter holding the active git-sha-pinned bundle name.
# Launch Template reads it at boot. `aws ssm put-parameter --overwrite`
# is the deploy-promotion / rollback knob.
#
# Type = String (not SecureString) — the SHA is not sensitive and
# StringList collisions with EC2's parameter-get pattern are avoided.

resource "aws_ssm_parameter" "bundle_sha" {
  name        = var.parameter_name
  description = "Active git-sha-pinned bundle name for the ${var.name_prefix} stack."
  type        = "String"
  value       = var.initial_value
  tier        = "Standard"

  # The deploy-promotion script overwrites the value; ignore drift so
  # terraform doesn't try to revert to bundle_sha_initial on each apply.
  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    Name = var.parameter_name
  }
}
