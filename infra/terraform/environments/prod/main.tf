# Aegis prod environment — mirrors the actual live deployment at aegisagent.in.
#
# IMPORTANT: every resource in this file matches an existing live resource
# discovered via aws describe-* at sprint-8 time. Apply this file ONLY after
# running the per-resource `terraform import` commands documented in
# IMPORT.md. Without import, `terraform apply` will FAIL because every name
# / ID already exists in the account.
#
# Inputs to keep in sync if AWS resources change outside Terraform:
#   - VPC CIDR / subnet CIDRs (network module)
#   - EC2 instance type (currently t3.2xlarge — over-provisioned; safe to
#     downsize to t3.large after import to save ~$200/month)
#   - RDS instance class (db.t3.micro Multi-AZ)
#   - Redis node type (cache.t3.micro single-node — yes, prod is also
#     single-node currently; that's a deliberate cost choice)

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
    key            = "prod/terraform.tfstate"
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
      Environment = "prod"
      ManagedBy   = "Terraform"
    }
  }
}

locals {
  name_prefix = "acp"
  common_tags = {
    Environment = "prod"
    Project     = "Aegis"
  }
}

# ──────────────────────────────────────────────────────────────────────
# Network — matches live VPC 10.0.0.0/16 (vpc-0b86b702b936fc905)
# ──────────────────────────────────────────────────────────────────────
module "network" {
  source               = "../../modules/network"
  name_prefix          = local.name_prefix
  vpc_cidr             = "10.0.0.0/16"
  availability_zones   = ["ap-south-1a", "ap-south-1b"]
  public_subnet_cidrs  = ["10.0.1.0/24", "10.0.2.0/24"]
  private_subnet_cidrs = ["10.0.3.0/24", "10.0.4.0/24"]
  tags                 = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# S3 — matches the 3 live buckets
# ──────────────────────────────────────────────────────────────────────
module "s3" {
  source      = "../../modules/s3"
  name_prefix = local.name_prefix
  buckets = {
    backups = {
      bucket_name        = "acp-backups-prod-am"
      versioning_enabled = true
      expiration_days    = 730 # 2-year retention for SOC2 audit window
    }
    backups_alt = {
      bucket_name        = "acp-backups-abhishek-prod"
      versioning_enabled = true
      expiration_days    = 365
    }
    statuspage = {
      bucket_name        = "aegis-statuspage"
      versioning_enabled = false
      expiration_days    = 0
      public_read        = true
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Secrets — same set as dev; prod values pre-exist and operators rotate
# via put-secret-value, not via Terraform
# ──────────────────────────────────────────────────────────────────────
module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = local.name_prefix
  secrets = {
    rds_master_password = {
      description   = "RDS master password — DO NOT rotate via terraform"
      initial_value = "REPLACE_ME_BEFORE_RDS_APPLY"
    }
    jwt_secret_key = {
      description = "JWT signing key — Aegis identity service"
    }
    internal_secret = {
      description = "Service-mesh internal secret"
    }
    groq_api_key = {
      description = "Groq API key for insight worker"
    }
    stripe_webhook_secret = {
      description   = "Stripe webhook signing secret"
      initial_value = "EMPTY"
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Security groups — match live state (acp-alb-sg, acp-ec2-sg, etc.)
# ──────────────────────────────────────────────────────────────────────
module "security_groups" {
  source      = "../../modules/security_groups"
  name_prefix = local.name_prefix
  vpc_id      = module.network.vpc_id
  # Live state has 22 + 8000 + 5173 open from operator IP. Inherit that
  # via ssh_allowed_cidrs and let the operator narrow it post-import.
  ssh_allowed_cidrs = var.ssh_allowed_cidrs
  gateway_port      = 8000
  ui_port           = 5173
  tags              = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# IAM — matches live acp-ec2-role attachments (SSM, CWAgent, S3FullAccess)
# Note: the live role has AmazonS3FullAccess which is over-scoped. Our
# module scopes S3 to specific bucket ARNs (a sprint-8.5 hardening); the
# operator-driven import can drop the FullAccess attachment after import.
# ──────────────────────────────────────────────────────────────────────
module "iam" {
  source      = "../../modules/iam"
  name_prefix = local.name_prefix
  s3_backup_bucket_arns = [
    module.s3.bucket_arns["backups"],
    module.s3.bucket_arns["backups_alt"],
  ]
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Compute — 2× t3.2xlarge spread across 1a / 1b (matches live)
# ──────────────────────────────────────────────────────────────────────
module "compute" {
  source                    = "../../modules/compute"
  name_prefix               = local.name_prefix
  instance_count            = 2
  instance_type             = var.ec2_instance_type
  subnet_ids                = module.network.public_subnet_ids
  vpc_security_group_ids    = [module.security_groups.ec2_sg_id]
  iam_instance_profile_name = module.iam.instance_profile_name
  root_volume_size_gb       = 100
  key_name                  = var.ec2_key_name
  tags                      = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# RDS — Postgres 15.18, Multi-AZ, db.t3.micro (matches live)
# ──────────────────────────────────────────────────────────────────────
module "rds" {
  source                       = "../../modules/rds"
  name_prefix                  = local.name_prefix
  identifier                   = "acp-postgres-prod"
  engine_version               = "15.18"
  instance_class               = "db.t3.micro"
  allocated_storage_gb         = 20
  max_allocated_storage_gb     = 200
  multi_az                     = true
  subnet_ids                   = module.network.private_subnet_ids
  vpc_security_group_ids       = [module.security_groups.rds_sg_id]
  master_password_secret_arn   = module.secrets.secret_arns["rds_master_password"]
  db_name                      = "acp"
  backup_retention_period_days = 7
  deletion_protection          = true # prod must not be destroyable by typo
  skip_final_snapshot          = false
  tags                         = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ElastiCache — single-node cache.t3.micro (matches live; upgrade path
# to a replication_group with MultiAZ is documented in module README)
# ──────────────────────────────────────────────────────────────────────
module "elasticache" {
  source                        = "../../modules/elasticache"
  name_prefix                   = local.name_prefix
  cluster_id                    = "acp-redis-prod"
  engine_version                = "7.1"
  node_type                     = "cache.t3.micro"
  num_cache_nodes               = 1
  subnet_ids                    = module.network.private_subnet_ids
  security_group_ids            = [module.security_groups.redis_sg_id]
  snapshot_retention_limit_days = 7
  tags                          = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ACM — matches live cert covering aegisagent.in + www + api
# ──────────────────────────────────────────────────────────────────────
module "acm" {
  source                    = "../../modules/acm"
  domain_name               = "aegisagent.in"
  subject_alternative_names = ["www.aegisagent.in", "api.aegisagent.in"]
  route53_zone_id           = var.route53_zone_id
  tags                      = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ALB — matches live acp-alb
# ──────────────────────────────────────────────────────────────────────
module "alb" {
  source              = "../../modules/alb"
  name_prefix         = local.name_prefix
  alb_name            = "acp-alb"
  vpc_id              = module.network.vpc_id
  subnet_ids          = module.network.public_subnet_ids
  security_group_ids  = [module.security_groups.alb_sg_id]
  target_port         = 5173
  health_check_path   = "/health"
  certificate_arn     = module.acm.validated_certificate_arn
  target_instance_ids = module.compute.instance_ids
  access_logs_bucket  = "" # currently disabled on live ALB; flip to bucket id when ready
  tags                = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Route53 — aegisagent.in + api.aegisagent.in (matches live)
# ──────────────────────────────────────────────────────────────────────
module "route53" {
  source  = "../../modules/route53"
  zone_id = var.route53_zone_id
  alias_records = {
    "aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
    "api.aegisagent.in" = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Budget alert — prod ceiling much higher than dev
# ──────────────────────────────────────────────────────────────────────
resource "aws_budgets_budget" "prod_cost_ceiling" {
  name              = "${local.name_prefix}-prod-monthly-cost"
  budget_type       = "COST"
  limit_amount      = "500.0"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-05-01_00:00"

  cost_filter {
    name   = "TagKeyValue"
    values = ["Environment$prod"]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.budget_alert_emails
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 90
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.budget_alert_emails
  }
}
