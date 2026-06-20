# Root composition. This is the ONLY file that calls modules.
# Modules do not call other modules.
#
# Provider default_tags apply project + environment + ManagedBy on every
# resource that supports tags. Per-resource tags add their own Name.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(
      {
        Project     = var.project
        Environment = var.environment
        ManagedBy   = "terraform"
      },
      var.tags,
    )
  }
}

# Existing resources — never created or destroyed by this stack.
data "aws_acm_certificate" "main" {
  domain      = var.domain
  statuses    = ["ISSUED"]
  most_recent = true
}

data "aws_route53_zone" "main" {
  name         = "${var.domain}."
  private_zone = false
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name_prefix = "${var.project}-${var.environment}"
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}

# ───────────────────────────────────────────────────────────────────────
# Network
# ───────────────────────────────────────────────────────────────────────
module "network" {
  source = "./modules/network"

  name_prefix          = local.name_prefix
  vpc_cidr             = var.vpc_cidr
  azs                  = var.azs
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  single_nat_gateway   = var.single_nat_gateway
}

# ───────────────────────────────────────────────────────────────────────
# Security Groups (4)
# ───────────────────────────────────────────────────────────────────────
module "security_groups" {
  source = "./modules/security_groups"

  name_prefix = local.name_prefix
  vpc_id      = module.network.vpc_id
}

# ───────────────────────────────────────────────────────────────────────
# IAM (EC2 instance profile + Secrets/S3/SSM/CW policies)
# ───────────────────────────────────────────────────────────────────────
module "iam" {
  source = "./modules/iam"

  name_prefix              = local.name_prefix
  bundle_bucket            = var.bundle_bucket
  public_roots_bucket      = var.public_roots_bucket
  secrets_arns             = module.secrets.all_secret_arns
  ssm_bundle_parameter_arn = module.ssm.bundle_parameter_arn
  app_param_arns           = module.params.parameter_arns
  audit_kms_key_arn        = module.audit_kms.key_arn
  log_group_arns           = module.log_groups.log_group_arns
}

# ───────────────────────────────────────────────────────────────────────
# Secrets (random_password — never set by hand)
# ───────────────────────────────────────────────────────────────────────
module "secrets" {
  source = "./modules/secrets"

  name_prefix = local.name_prefix
}

# ───────────────────────────────────────────────────────────────────────
# Route 53 (apex A + AAAA + www CNAME against the existing ALB)
# ───────────────────────────────────────────────────────────────────────
module "route53" {
  source = "./modules/route53"

  zone_id      = data.aws_route53_zone.main.zone_id
  domain       = var.domain
  alb_dns_name = module.alb.dns_name
  alb_zone_id  = module.alb.zone_id
}

# ───────────────────────────────────────────────────────────────────────
# WAF (AWS managed core + bot + per-IP rate limit) — attached to ALB.
# ───────────────────────────────────────────────────────────────────────
module "waf" {
  source = "./modules/waf"

  name_prefix         = local.name_prefix
  rate_limit_per_5min = var.waf_rate_limit_per_ip_per_5min
  alb_arn             = module.alb.arn
}

# ───────────────────────────────────────────────────────────────────────
# ALB (HTTP→HTTPS redirect + HTTPS listener + target group)
# ───────────────────────────────────────────────────────────────────────
module "alb" {
  source = "./modules/alb"

  name_prefix         = local.name_prefix
  vpc_id              = module.network.vpc_id
  public_subnet_ids   = module.network.public_subnet_ids
  alb_security_group  = module.security_groups.alb_sg_id
  acm_certificate_arn = data.aws_acm_certificate.main.arn
  alb_log_bucket      = module.s3.alb_logs_bucket
  log_retention_days  = var.alb_log_retention_days
}

# ───────────────────────────────────────────────────────────────────────
# ASG (Launch Template reads SSM Param at boot)
# ───────────────────────────────────────────────────────────────────────
module "asg" {
  source = "./modules/asg"

  name_prefix          = local.name_prefix
  private_subnet_ids   = module.network.private_subnet_ids
  ec2_security_group   = module.security_groups.ec2_sg_id
  instance_profile     = module.iam.instance_profile_name
  instance_type        = var.instance_type
  asg_min              = var.asg_min
  asg_max              = var.asg_max
  asg_desired          = var.asg_desired
  target_group_arn     = module.alb.target_group_arn
  ssm_bundle_parameter = module.ssm.bundle_parameter_name
  bundle_bucket        = var.bundle_bucket
  aws_region           = var.aws_region

  rds_endpoint             = module.rds.endpoint
  rds_master_secret_id     = module.secrets.db_password_name
  redis_primary_endpoint   = module.elasticache.primary_endpoint
  domain                   = var.domain
  internal_secret_arn      = module.secrets.internal_secret_arn
  jwt_signing_secret_id    = module.secrets.jwt_signing_name
  mesh_jwt_secret_id       = module.secrets.mesh_jwt_secret_arn
  stripe_webhook_secret_id = module.secrets.stripe_webhook_secret_arn
  groq_api_key_secret_id   = module.secrets.groq_api_key_arn
  app_param_prefix         = var.app_param_prefix
  public_roots_bucket      = var.public_roots_bucket
}

# ───────────────────────────────────────────────────────────────────────
# RDS (Postgres Multi-AZ + parameter group + final snapshot)
# ───────────────────────────────────────────────────────────────────────
module "rds" {
  source = "./modules/rds"

  name_prefix            = local.name_prefix
  vpc_id                 = module.network.vpc_id
  private_subnet_ids     = module.network.private_subnet_ids
  rds_security_group     = module.security_groups.rds_sg_id
  master_password_secret = module.secrets.db_password_arn
  engine_version         = var.db_engine_version
  instance_class         = var.db_instance_class
  allocated_storage      = var.db_allocated_storage
  max_allocated_storage  = var.db_max_allocated_storage
  backup_retention       = var.db_backup_retention
  multi_az               = var.db_multi_az
}

# ───────────────────────────────────────────────────────────────────────
# ElastiCache Redis (primary + 1 replica, no cluster mode)
# ───────────────────────────────────────────────────────────────────────
module "elasticache" {
  source = "./modules/elasticache"

  name_prefix          = local.name_prefix
  vpc_id               = module.network.vpc_id
  private_subnet_ids   = module.network.private_subnet_ids
  redis_security_group = module.security_groups.redis_sg_id
  node_type            = var.redis_node_type
  num_nodes            = var.redis_num_nodes
}

# ───────────────────────────────────────────────────────────────────────
# S3 (3 buckets created + 1 imported)
# ───────────────────────────────────────────────────────────────────────
module "s3" {
  source = "./modules/s3"

  name_prefix         = local.name_prefix
  account_id          = data.aws_caller_identity.current.account_id
  alb_log_retention   = var.alb_log_retention_days
  public_roots_bucket = var.public_roots_bucket
  bundle_bucket       = var.bundle_bucket
}

# ───────────────────────────────────────────────────────────────────────
# CloudWatch (3 alarms + SNS topic)
# ───────────────────────────────────────────────────────────────────────
module "cloudwatch" {
  source = "./modules/cloudwatch"

  name_prefix             = local.name_prefix
  alarm_email             = var.sns_alarm_email
  alb_arn_suffix          = module.alb.arn_suffix
  target_group_arn_suffix = module.alb.target_group_arn_suffix
  rds_instance_identifier = module.rds.instance_id
}

# ───────────────────────────────────────────────────────────────────────
# SSM (bundle SHA parameter — ASG reads at boot)
# ───────────────────────────────────────────────────────────────────────
module "ssm" {
  source = "./modules/ssm"

  parameter_name = var.ssm_bundle_parameter
  initial_value  = var.bundle_sha_initial
  name_prefix    = local.name_prefix
}

# ───────────────────────────────────────────────────────────────────────
# SSM Parameters (app config — Clerk, Stripe, Anthropic, etc.)
# ignore_changes on value; operator fills via aws ssm put-parameter.
# ───────────────────────────────────────────────────────────────────────
module "params" {
  source = "./modules/params"

  env_prefix  = var.app_param_prefix
  name_prefix = local.name_prefix
}

# ───────────────────────────────────────────────────────────────────────
# Audit-envelope KMS CMK (alias/aegis-audit-envelope)
# ───────────────────────────────────────────────────────────────────────
module "audit_kms" {
  source = "./modules/audit_kms"

  name_prefix  = local.name_prefix
  alias_name   = var.audit_kms_alias
  ec2_role_arn = module.iam.ec2_role_arn
}

# ───────────────────────────────────────────────────────────────────────
# Log groups (created upfront so CW agent has writable targets at boot)
# ───────────────────────────────────────────────────────────────────────
module "log_groups" {
  source = "./modules/log_groups"

  name_prefix = local.name_prefix
}

# ───────────────────────────────────────────────────────────────────────
# CloudTrail (management events to the cloudtrail bucket)
# ───────────────────────────────────────────────────────────────────────
module "cloudtrail" {
  source = "./modules/cloudtrail"

  name_prefix            = local.name_prefix
  cloudtrail_bucket_name = module.s3.cloudtrail_bucket
}
