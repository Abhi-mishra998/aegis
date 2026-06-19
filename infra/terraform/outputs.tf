# Operator-readable outputs. After `terraform apply`, these are what
# the operator copies into deploy scripts, customer comms, dashboards.

output "alb_dns_name" {
  description = "ALB DNS hostname. Route 53 apex A record points here."
  value       = module.alb.dns_name
}

output "alb_zone_id" {
  description = "ALB Route 53 zone id (needed for apex A alias records)."
  value       = module.alb.zone_id
}

output "vpc_id" {
  description = "Primary VPC id."
  value       = module.network.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnet ids (ALB + NAT)."
  value       = module.network.public_subnet_ids
}

output "private_subnet_ids" {
  description = "Private subnet ids (EC2 + RDS + Redis)."
  value       = module.network.private_subnet_ids
}

output "asg_name" {
  description = "Auto Scaling Group name. Use with `aws autoscaling start-instance-refresh`."
  value       = module.asg.name
}

output "ssm_bundle_parameter_name" {
  description = "SSM Parameter name holding the active bundle SHA. Use with `aws ssm put-parameter`."
  value       = module.ssm.bundle_parameter_name
}

output "bundle_bucket" {
  description = "S3 bucket name holding releases/bundle-<sha>.tar.gz objects."
  value       = var.bundle_bucket
}

output "rds_endpoint" {
  description = "RDS connection endpoint (host:port)."
  value       = module.rds.endpoint
}

output "rds_resource_id" {
  description = "RDS DbiResourceId for IAM-DB-auth grants and audit binding."
  value       = module.rds.resource_id
}

output "redis_primary_endpoint" {
  description = "ElastiCache primary endpoint (read+write)."
  value       = module.elasticache.primary_endpoint
}

output "redis_reader_endpoint" {
  description = "ElastiCache reader endpoint (read-only across replicas)."
  value       = module.elasticache.reader_endpoint
}

output "secrets_arn_db_password" {
  description = "Secrets Manager ARN for the DB master password. EC2 instance profile reads this."
  value       = module.secrets.db_password_arn
}

output "secrets_arn_jwt_signing_key" {
  description = "Secrets Manager ARN for the JWT signing key."
  value       = module.secrets.jwt_signing_arn
}

output "sns_alarm_topic_arn" {
  description = "SNS topic that receives CloudWatch alarm notifications."
  value       = module.cloudwatch.sns_topic_arn
}

output "waf_web_acl_arn" {
  description = "WAFv2 Web ACL ARN attached to the ALB."
  value       = module.waf.web_acl_arn
}

output "domain" {
  description = "Public apex domain served by the ALB."
  value       = var.domain
}

output "audit_kms_alias" {
  description = "Audit-envelope KMS alias (alias/...). Application reads this."
  value       = module.audit_kms.alias_name
}

output "audit_kms_key_arn" {
  description = "Audit-envelope KMS key ARN."
  value       = module.audit_kms.key_arn
}

output "app_param_names" {
  description = "Map of logical -> SSM path for every app-config parameter."
  value       = module.params.parameter_names
}

output "log_group_arns" {
  description = "CloudWatch log group ARNs created by the stack."
  value       = module.log_groups.log_group_arns
}

output "cloudtrail_trail_arn" {
  description = "Management-events CloudTrail ARN."
  value       = module.cloudtrail.trail_arn
}
