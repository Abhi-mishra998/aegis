output "alb_dns_name" {
  value = module.alb.alb_dns_name
}

output "dev_url" {
  value = "https://${var.dev_hostname}"
}

output "ec2_instance_ids" {
  value = module.compute.instance_ids
}

output "ec2_public_ips" {
  value = module.compute.public_ips
}

output "rds_address" {
  value     = module.rds.address
  sensitive = true
}

output "redis_endpoint" {
  value     = module.elasticache.primary_endpoint
  sensitive = true
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "secret_names" {
  description = "Operator must populate the real value via `aws secretsmanager put-secret-value` before bringing the application up."
  value       = module.secrets.secret_names
}

output "next_steps" {
  description = "Post-apply operator checklist"
  value       = <<-EOT
    1. Populate secrets (use aws secretsmanager put-secret-value --secret-id <name>):
       - acp-dev/rds_master_password   (REQUIRED before connecting to RDS)
       - acp-dev/jwt_secret_key        (REQUIRED before starting the app)
       - acp-dev/internal_secret       (REQUIRED before starting the app)
       - acp-dev/groq_api_key          (optional — leave EMPTY to disable Groq)
       - acp-dev/stripe_webhook_secret (optional — leave EMPTY to disable billing)

    2. SSH into the EC2 host (via SSM Session Manager):
       aws ssm start-session --target $(terraform output -raw ec2_instance_ids | jq -r '.[0]')

    3. Run the bootstrap script on the host:
       sudo bash /opt/aegis/scripts/ops/bootstrap_new_host.sh

    4. Smoke-test:
       GATEWAY_URL=https://${var.dev_hostname} ADMIN_JWT=... ./scripts/ops/smoke_test.sh
  EOT
}
