# Terraform partial-backend config for the EU instance.
#
# Sprint EI-5 (2026-06-20). The root backend.tf hard-codes the prod
# state bucket; this file overrides bucket + key + region so the EU
# stack's state file lives IN EU (not replicated to ap-south-1).
# Without this, the EU instance's state would touch ap-south-1 S3,
# breaking the "EU data never leaves EU" claim.
#
# Use:
#   cd infra/terraform
#   terraform init -reconfigure \
#     -backend-config=envs/eu-west-1/backend-eu-west-1.hcl
#   terraform apply -var-file=envs/eu-west-1/terraform.tfvars
#
# After EU apply, run:
#   terraform init -reconfigure   # restores prod backend
# before any ap-south-1 work; the two state files are completely
# separate and an accidental cross-region apply would corrupt one or
# both.

bucket       = "aegis-terraform-state-eu-628478946931"
key          = "eu/terraform.tfstate"
region       = "eu-west-1"
encrypt      = true
use_lockfile = true
