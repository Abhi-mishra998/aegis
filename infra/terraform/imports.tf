# Declarative imports (Terraform 1.5+).
#
# At apply time, Terraform finds the named resource already exists and
# adopts it into state instead of creating a duplicate. After the first
# successful apply, this file can be left in place (no-op on later runs)
# or deleted — leaving it is documentation.
#
# To check the current resource attributes are configured exactly as our
# terraform expects:
#
#   terraform plan -var-file=envs/prod/terraform.tfvars
#
# Any drift between the live attributes and our resource block shows up
# as a `change`. If something there is intentional, update our code to
# match. If something there is unintentional, the operator decides:
# overwrite (apply) or fix the live resource first.

import {
  to = module.s3.aws_s3_bucket.public_roots
  id = "aegis-public-roots-628478946931"
}
