# Aegis prod-ha — the HA target environment that closes the audit's S7
# finding: every component a single point of failure.
#
# Sprint 9 ships this as a SECOND prod stack (not a refactor of the
# existing prod/ which mirrors imported live resources). Cut-over is
# the operator's call — see docs/runbooks/prod-ha-cutover.md (sibling
# of this directory).
#
# What changed vs prod/:
#   * App tier moves from 2× public-subnet EC2 → ASG (min=2,max=6) in
#     PRIVATE subnets.
#   * Network gets per-AZ NAT gateways so the private fleet can reach
#     AWS APIs (KMS, SSM, Secrets Manager).
#   * Redis moves from single-node cache.t3.micro → replication group
#     with multi-AZ + automatic failover + at-rest + in-transit
#     encryption.
#   * RDS keeps multi-AZ but moves to db.t3.medium (the t3.micro in
#     prod/ is right-sized for the import, not the workload).
#   * ALB gets a WAFv2 web ACL (Common + KnownBadInputs + SQLi + rate
#     limit) and access logging enabled.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
  }

  backend "s3" {
    bucket         = "aegis-terraform-state-628478946931"
    key            = "prod-ha/terraform.tfstate"
    region         = "ap-south-1"
    dynamodb_table = "aegis-terraform-locks"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "Aegis"
      Environment = "prod-ha"
      ManagedBy   = "Terraform"
      Sprint      = "9"
    }
  }
}

locals {
  name_prefix = "acp-prodha"
  common_tags = {
    Environment = "prod-ha"
    Project     = "Aegis"
    Sprint      = "9"
  }
}

# ──────────────────────────────────────────────────────────────────────
# Network — 2 AZs, per-AZ NAT, encrypted everywhere.
# ──────────────────────────────────────────────────────────────────────
module "network" {
  source               = "../../modules/network"
  name_prefix          = local.name_prefix
  vpc_cidr             = "10.20.0.0/16"
  availability_zones   = ["ap-south-1a", "ap-south-1b"]
  public_subnet_cidrs  = ["10.20.1.0/24", "10.20.2.0/24"]
  private_subnet_cidrs = ["10.20.3.0/24", "10.20.4.0/24"]
  enable_nat_gateways  = true
  # 20-user testing infra: single shared NAT saves ~$32/mo. For real
  # production load set one_nat_per_az=true to remove the AZ-A SPOF.
  one_nat_per_az = false
  tags           = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# S3 — same buckets as prod (backups, alb_logs).
# ──────────────────────────────────────────────────────────────────────
module "s3" {
  source      = "../../modules/s3"
  name_prefix = local.name_prefix
  aws_region  = var.aws_region
  buckets = {
    backups = {
      bucket_name        = "acp-backups-prodha-${var.bucket_suffix}"
      versioning_enabled = true
      expiration_days    = 730
    }
    alb_logs = {
      bucket_name        = "acp-alb-logs-prodha-${var.bucket_suffix}"
      versioning_enabled = false
      expiration_days    = 30
      alb_log_delivery   = true
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Secrets Manager — RDS password, JWT secret, internal secret. KMS-encrypted
# at rest via the aws-managed CMK by default; operators MAY swap to a
# customer-managed CMK via the module's kms_key_id input.
# ──────────────────────────────────────────────────────────────────────
module "secrets" {
  source                  = "../../modules/secrets"
  name_prefix             = local.name_prefix
  recovery_window_in_days = 30 # prod-ha keeps the 30-day soft-delete safety net
  secrets = {
    rds_master_password = {
      description   = "RDS master password — populated by operator post-apply"
      initial_value = "REPLACE_ME_BEFORE_RDS_APPLY"
    }
    jwt_secret_key = {
      description = "JWT signing key — identity service"
    }
    internal_secret = {
      description = "Service-mesh internal secret"
    }
    mesh_jwt_secret = {
      description = "Per-service mesh JWT signing key (Sprint 1.4)"
    }
    redis_auth_token = {
      description   = "ElastiCache AUTH token (in-transit Redis encryption)"
      initial_value = "GENERATE_AND_PUT"
    }
    groq_api_key = {
      description   = "Groq API key (optional)"
      initial_value = "EMPTY"
    }
    stripe_webhook_secret = {
      description   = "Stripe webhook signing secret"
      initial_value = "EMPTY"
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Security groups — ALB ingress 443 from world; ASG ingress 5173 from
# ALB SG only; RDS + Redis ingress from ASG SG only.
# ──────────────────────────────────────────────────────────────────────
module "security_groups" {
  source            = "../../modules/security_groups"
  name_prefix       = local.name_prefix
  vpc_id            = module.network.vpc_id
  ssh_allowed_cidrs = [] # SSM Session Manager — no direct SSH ingress
  gateway_port      = 8000
  ui_port           = 5173
  tags              = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# IAM — instance profile with SSM, KMS decrypt, Secrets Manager read,
# S3 backup PutObject.
# ──────────────────────────────────────────────────────────────────────
module "iam" {
  source      = "../../modules/iam"
  name_prefix = local.name_prefix
  s3_backup_bucket_arns = [
    "arn:aws:s3:::${module.s3.bucket_ids["backups"]}",
    "arn:aws:s3:::${module.s3.bucket_ids["backups"]}/*",
  ]
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# RDS — Postgres 15.18 Multi-AZ on db.t3.medium.
# ──────────────────────────────────────────────────────────────────────
module "rds" {
  source         = "../../modules/rds"
  name_prefix    = local.name_prefix
  identifier     = "${local.name_prefix}-postgres"
  engine_version = "15.18"
  instance_class = var.rds_instance_class
  # 20-user testing infra: 30 GB initial, auto-scale up to 100 GB.
  # Bump to 100 / 500 for production load.
  allocated_storage_gb         = 30
  max_allocated_storage_gb     = 100
  multi_az                     = true
  subnet_ids                   = module.network.private_subnet_ids
  vpc_security_group_ids       = [module.security_groups.rds_sg_id]
  master_password_secret_arn   = module.secrets.secret_arns["rds_master_password"]
  db_name                      = "acp"
  backup_retention_period_days = 7
  deletion_protection          = true
  skip_final_snapshot          = false
  tags                         = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Redis — multi-AZ replication group with cross-AZ failover, encryption
# at rest + in transit, daily snapshots retained 7 days.
# ──────────────────────────────────────────────────────────────────────
module "redis" {
  source                        = "../../modules/elasticache_ha"
  name_prefix                   = local.name_prefix
  replication_group_id          = "${local.name_prefix}-redis"
  description                   = "Aegis prod-ha Redis (Sprint 9)"
  engine_version                = "7.1"
  node_type                     = var.redis_node_type
  num_node_groups               = 1
  replicas_per_node_group       = var.redis_replicas_per_node_group
  subnet_ids                    = module.network.private_subnet_ids
  security_group_ids            = [module.security_groups.redis_sg_id]
  snapshot_retention_limit_days = 7
  auth_token_secret_arn         = module.secrets.secret_arns["redis_auth_token"]
  tags                          = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ACM cert — apex (aegisagent.in) + www + api + ha + dev + a wildcard for
# subdomains. After prod-ha apply this single ALB serves all of them.
# ──────────────────────────────────────────────────────────────────────
module "acm" {
  source      = "../../modules/acm"
  domain_name = "aegisagent.in"
  subject_alternative_names = [
    "www.aegisagent.in",
    "api.aegisagent.in",
    "ha.aegisagent.in",
    "dev.aegisagent.in",
    "*.aegisagent.in",
  ]
  route53_zone_id = var.route53_zone_id
  tags            = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ALB in PUBLIC subnets — ASG targets attach to its target group.
# ──────────────────────────────────────────────────────────────────────
module "alb" {
  source              = "../../modules/alb"
  name_prefix         = local.name_prefix
  alb_name            = "${local.name_prefix}-alb"
  vpc_id              = module.network.vpc_id
  subnet_ids          = module.network.public_subnet_ids
  security_group_ids  = [module.security_groups.alb_sg_id]
  target_port         = 5173
  health_check_path   = "/health"
  certificate_arn     = module.acm.validated_certificate_arn
  target_instance_ids = [] # ASG attaches instances dynamically — no static targets
  access_logs_bucket  = module.s3.bucket_ids["alb_logs"]
  tags                = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# WAFv2 — Common + KnownBadInputs + SQLi + per-IP rate limit.
# ──────────────────────────────────────────────────────────────────────
module "waf" {
  source              = "../../modules/waf"
  name_prefix         = local.name_prefix
  alb_arn             = module.alb.alb_arn
  rate_limit_per_5min = var.waf_rate_limit_per_5min
  ip_allowlist_cidrs  = var.waf_ip_allowlist
  tags                = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ASG — autoscaled application tier in PRIVATE subnets.
# ──────────────────────────────────────────────────────────────────────
module "asg" {
  source                    = "../../modules/asg"
  name_prefix               = local.name_prefix
  ami_id                    = var.ec2_ami_id
  instance_type             = var.ec2_instance_type
  key_name                  = var.ec2_key_name
  subnet_ids                = module.network.private_subnet_ids
  vpc_security_group_ids    = [module.security_groups.ec2_sg_id]
  iam_instance_profile_name = module.iam.instance_profile_name
  alb_target_group_arn      = module.alb.target_group_arn
  min_size                  = var.asg_min_size
  desired_capacity          = var.asg_desired_capacity
  max_size                  = var.asg_max_size
  user_data                 = file("${path.module}/user_data.sh")
  tags                      = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Route53 — ha.aegisagent.in → prod-ha ALB.
# ──────────────────────────────────────────────────────────────────────
module "route53" {
  source  = "../../modules/route53"
  zone_id = var.route53_zone_id
  alias_records = {
    "aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
    "www.aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
    "api.aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
    "ha.aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
    "dev.aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Budget alert — 20-user testing infra.
# Baseline projection ~$195/mo; ceiling 300 alerts at 80% ($240/mo).
# Bump to $1500 when scaling to real customer load.
# ──────────────────────────────────────────────────────────────────────
resource "aws_budgets_budget" "prod_ha_cost_ceiling" {
  name              = "${local.name_prefix}-monthly-cost"
  budget_type       = "COST"
  limit_amount      = "300.0"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-07-01_00:00"

  cost_filter {
    name   = "TagKeyValue"
    values = ["Environment$prod-ha"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = ["abhishek@aegisagent.in"]
  }
}
