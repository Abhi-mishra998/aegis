# Aegis dev environment — sized for a 10-user test portal.
# Target monthly spend (ap-south-1, on-demand): ~$55. See README for breakdown.
#
# Differences from prod:
#   - 1 EC2 instance (t3.small), not 2 (t3.2xlarge)
#   - RDS Single-AZ db.t4g.micro (cheapest Graviton class), not Multi-AZ db.t3.micro
#   - Redis single-node cache.t3.micro (matches prod node type, no replica)
#   - No deletion protection — iteration is cheap
#   - Smaller storage (RDS 20 GB, EC2 50 GB root)
#   - No final snapshot on destroy
#   - 7-day backup retention on RDS, none on Redis

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
    key            = "dev/terraform.tfstate"
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
      Environment = "dev"
      ManagedBy   = "Terraform"
      CostCenter  = "test-portal"
    }
  }
}

locals {
  name_prefix = "acp-dev"
  common_tags = {
    Environment = "dev"
    Project     = "Aegis"
  }
}

# ──────────────────────────────────────────────────────────────────────
# Network — VPC + 2 AZs (RDS requires at least 2 subnet AZs even single-AZ)
# ──────────────────────────────────────────────────────────────────────
module "network" {
  source             = "../../modules/network"
  name_prefix        = local.name_prefix
  vpc_cidr           = "10.10.0.0/16"
  availability_zones = ["ap-south-1a", "ap-south-1b"]
  # Different CIDRs from prod (10.0/16) so the two VPCs can peer if needed.
  public_subnet_cidrs  = ["10.10.1.0/24", "10.10.2.0/24"]
  private_subnet_cidrs = ["10.10.3.0/24", "10.10.4.0/24"]
  tags                 = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# S3 buckets
# ──────────────────────────────────────────────────────────────────────
module "s3" {
  source      = "../../modules/s3"
  name_prefix = local.name_prefix
  buckets = {
    backups = {
      bucket_name        = "acp-dev-backups-${var.bucket_suffix}"
      versioning_enabled = true
      expiration_days    = 30 # dev backups are short-lived
    }
    statuspage = {
      bucket_name        = "acp-dev-statuspage-${var.bucket_suffix}"
      versioning_enabled = false
      expiration_days    = 30
      public_read        = true
    }
    alb_logs = {
      bucket_name        = "acp-dev-alb-logs-${var.bucket_suffix}"
      versioning_enabled = false
      expiration_days    = 14
      alb_log_delivery   = true
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Secrets — Postgres master password, JWT secret, internal-mesh secret
# ──────────────────────────────────────────────────────────────────────
module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = local.name_prefix
  secrets = {
    rds_master_password = {
      description   = "RDS master password — populated by operator post-apply"
      initial_value = "REPLACE_ME_BEFORE_RDS_APPLY"
    }
    jwt_secret_key = {
      description = "JWT signing key — Aegis identity service"
    }
    internal_secret = {
      description = "Service-mesh internal secret — Aegis inter-service auth"
    }
    groq_api_key = {
      description   = "Groq API key for insight worker (optional)"
      initial_value = "EMPTY"
    }
    stripe_webhook_secret = {
      description   = "Stripe webhook signing secret (sprint-5.3)"
      initial_value = "EMPTY"
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Security groups
# ──────────────────────────────────────────────────────────────────────
module "security_groups" {
  source            = "../../modules/security_groups"
  name_prefix       = local.name_prefix
  vpc_id            = module.network.vpc_id
  ssh_allowed_cidrs = var.ssh_allowed_cidrs # default [] — disables SSH; use SSM
  gateway_port      = 8000
  ui_port           = 5173
  tags              = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# IAM — EC2 instance role scoped to the dev backup bucket only
# ──────────────────────────────────────────────────────────────────────
module "iam" {
  source                = "../../modules/iam"
  name_prefix           = local.name_prefix
  s3_backup_bucket_arns = [module.s3.bucket_arns["backups"]]
  tags                  = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Compute — 1× t3.small EC2 in a public subnet (no NAT cost)
# ──────────────────────────────────────────────────────────────────────
module "compute" {
  source         = "../../modules/compute"
  name_prefix    = local.name_prefix
  instance_count = 1 # one host for 10 users is plenty
  # Graviton m6g.medium: 1 vCPU / 4 GB, ~$28/month. t4g.small (2 GB) OOM-ed
  # under the 14-container Aegis stack; ap-south-1a returned
  # InsufficientInstanceCapacity for t4g.medium and t4g.large on the resize
  # attempt, so m6g.medium was the available 4 GB Graviton sibling.
  instance_type             = "m6g.medium"
  subnet_ids                = module.network.public_subnet_ids
  vpc_security_group_ids    = [module.security_groups.ec2_sg_id]
  iam_instance_profile_name = module.iam.instance_profile_name
  root_volume_size_gb       = 50
  key_name                  = var.ec2_key_name
  tags                      = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# RDS — single-AZ Postgres on db.t4g.micro (cheapest Graviton class)
# ──────────────────────────────────────────────────────────────────────
module "rds" {
  source                       = "../../modules/rds"
  name_prefix                  = local.name_prefix
  identifier                   = "acp-postgres-dev"
  engine_version               = "15.18"
  instance_class               = "db.t4g.micro"
  allocated_storage_gb         = 20
  max_allocated_storage_gb     = 50
  multi_az                     = false # cost saver
  subnet_ids                   = module.network.private_subnet_ids
  vpc_security_group_ids       = [module.security_groups.rds_sg_id]
  master_password_secret_arn   = module.secrets.secret_arns["rds_master_password"]
  db_name                      = "acp"
  backup_retention_period_days = 7
  deletion_protection          = false
  skip_final_snapshot          = true
  tags                         = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ElastiCache — single Redis node (no failover; minimal cost)
# ──────────────────────────────────────────────────────────────────────
module "elasticache" {
  source                        = "../../modules/elasticache"
  name_prefix                   = local.name_prefix
  cluster_id                    = "acp-redis-dev"
  engine_version                = "7.1"
  node_type                     = "cache.t3.micro"
  num_cache_nodes               = 1
  subnet_ids                    = module.network.private_subnet_ids
  security_group_ids            = [module.security_groups.redis_sg_id]
  snapshot_retention_limit_days = 0 # dev doesn't need snapshots
  tags                          = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ACM cert for the dev hostname — DNS validation
# ──────────────────────────────────────────────────────────────────────
module "acm" {
  source                    = "../../modules/acm"
  domain_name               = var.dev_hostname # e.g. dev.aegisagent.in
  subject_alternative_names = []
  route53_zone_id           = var.route53_zone_id
  tags                      = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# ALB — internet-facing on 80/443, target group → ec2:5173 (UI nginx)
# ──────────────────────────────────────────────────────────────────────
module "alb" {
  source             = "../../modules/alb"
  name_prefix        = local.name_prefix
  alb_name           = "acp-dev-alb"
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.public_subnet_ids
  security_group_ids = [module.security_groups.alb_sg_id]
  target_port        = 5173
  # U13 — `/healthz` nginx-proxies to gateway:8000/health (2s timeout).
  # Previously `/health` was a static nginx 200, so a dead gateway behind
  # a healthy nginx stayed registered and got live traffic.
  health_check_path   = "/healthz"
  certificate_arn     = module.acm.validated_certificate_arn
  target_instance_ids = module.compute.instance_ids
  access_logs_bucket  = module.s3.bucket_ids["alb_logs"]
  tags                = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Route53 — alias dev.aegisagent.in → ALB
# ──────────────────────────────────────────────────────────────────────
module "route53" {
  source  = "../../modules/route53"
  zone_id = var.route53_zone_id
  alias_records = {
    (var.dev_hostname) = {
      target_dns_name = module.alb.alb_dns_name
      target_zone_id  = module.alb.alb_zone_id
    }
  }
  tags = local.common_tags
}

# ──────────────────────────────────────────────────────────────────────
# Budget alert — paging at $60/month
# ──────────────────────────────────────────────────────────────────────
resource "aws_budgets_budget" "dev_cost_ceiling" {
  name              = "${local.name_prefix}-monthly-cost"
  budget_type       = "COST"
  limit_amount      = "60.0"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-05-01_00:00"

  cost_filter {
    name   = "TagKeyValue"
    values = ["Environment$dev"]
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
}
