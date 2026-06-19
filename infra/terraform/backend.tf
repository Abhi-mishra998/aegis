# S3 backend with native S3 locking (use_lockfile = true).
#
# Why no DynamoDB lock: Terraform 1.10+ supports native S3 locking via
# `use_lockfile = true`. It writes a `<key>.tflock` object beside the
# state file and uses conditional writes for mutual exclusion. Gives
# the same safety as a DynamoDB lock table at $0/mo and one less
# resource to manage.
#
# When to migrate to DynamoDB: only if you hit S3 conditional-write
# limits (effectively never for a solo founder), OR if you cross-region
# the state and need lower-latency locks elsewhere.

terraform {
  backend "s3" {
    bucket       = "aegis-terraform-state-628478946931"
    key          = "prod/terraform.tfstate"
    region       = "ap-south-1"
    encrypt      = true
    use_lockfile = true
  }
}
