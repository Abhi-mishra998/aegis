# Top-level inputs. envs/prod/terraform.tfvars supplies values.
# Every variable is explicitly typed and documented; no hidden defaults
# that change behaviour at scale.

# ─── Identity ───────────────────────────────────────────────────────────
variable "aws_region" {
  description = "AWS region for every resource."
  type        = string
  default     = "ap-south-1"
}

variable "project" {
  description = "Project tag prefix on every resource (e.g. 'aegis')."
  type        = string
  default     = "aegis"
}

variable "environment" {
  description = "Environment tag — appended to project to compose names."
  type        = string
  default     = "prod"
}

variable "domain" {
  description = "Apex domain served by the ALB. Must already exist as a Route 53 hosted zone."
  type        = string
}

# ─── Network ────────────────────────────────────────────────────────────
variable "vpc_cidr" {
  description = "Primary VPC CIDR."
  type        = string
}

variable "azs" {
  description = "AZs used for the 4 subnets (must be exactly 2; ALB needs 2 AZs)."
  type        = list(string)
  validation {
    condition     = length(var.azs) == 2
    error_message = "Provide exactly 2 AZs — the ALB requires it."
  }
}

variable "public_subnet_cidrs" {
  description = "CIDRs for the 2 public subnets (ALB + NAT)."
  type        = list(string)
  validation {
    condition     = length(var.public_subnet_cidrs) == 2
    error_message = "Provide exactly 2 public subnet CIDRs."
  }
}

variable "private_subnet_cidrs" {
  description = "CIDRs for the 2 private subnets (EC2 + RDS + Redis)."
  type        = list(string)
  validation {
    condition     = length(var.private_subnet_cidrs) == 2
    error_message = "Provide exactly 2 private subnet CIDRs."
  }
}

variable "single_nat_gateway" {
  description = "Use ONE NAT Gateway shared across AZs (saves ~$33/mo; 1a outage kills outbound)."
  type        = bool
  default     = true
}

# ─── Compute ────────────────────────────────────────────────────────────
variable "instance_type" {
  description = "EC2 instance type for the ASG."
  type        = string
  default     = "m6g.large"
}

variable "asg_min" {
  description = "ASG minimum size."
  type        = number
  default     = 2
}

variable "asg_max" {
  description = "ASG maximum size."
  type        = number
  default     = 4
}

variable "asg_desired" {
  description = "ASG desired capacity."
  type        = number
  default     = 2
}

# ─── Bundle ─────────────────────────────────────────────────────────────
variable "ssm_bundle_parameter" {
  description = "SSM Parameter name holding the active git-sha-pinned bundle name."
  type        = string
}

variable "bundle_bucket" {
  description = "S3 bucket name holding release tarballs (releases/bundle-<sha>.tar.gz)."
  type        = string
}

variable "bundle_sha_initial" {
  description = "First SHA the SSM Parameter is seeded with. ASG boots this bundle."
  type        = string
}

# ─── Database ───────────────────────────────────────────────────────────
variable "db_engine_version" {
  description = "Postgres engine version."
  type        = string
  default     = "15.7"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t3.small"
}

variable "db_allocated_storage" {
  description = "Initial gp3 storage in GB."
  type        = number
  default     = 50
}

variable "db_max_allocated_storage" {
  description = "Auto-grow ceiling for gp3 storage."
  type        = number
  default     = 200
}

variable "db_backup_retention" {
  description = "Days of automated backups RDS retains."
  type        = number
  default     = 14
}

variable "db_multi_az" {
  description = "RDS Multi-AZ for in-region failover."
  type        = bool
  default     = true
}

# ─── Redis ──────────────────────────────────────────────────────────────
variable "redis_node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_num_nodes" {
  description = "Total nodes in the replication group (1 primary + N-1 replicas)."
  type        = number
  default     = 2
}

# ─── Edge ───────────────────────────────────────────────────────────────
variable "alb_log_retention_days" {
  description = "CloudWatch / S3 retention for ALB access logs."
  type        = number
  default     = 30
}

# ─── WAF ────────────────────────────────────────────────────────────────
variable "waf_rate_limit_per_ip_per_5min" {
  description = "WAFv2 per-IP rate limit over a 5-minute window."
  type        = number
  default     = 2000
}

# ─── Observability ──────────────────────────────────────────────────────
variable "sns_alarm_email" {
  description = "Email address that receives CloudWatch alarm SNS notifications."
  type        = string
}

# ─── Preserved resources ────────────────────────────────────────────────
variable "public_roots_bucket" {
  description = "Existing customer-visible transparency bucket. NEVER destroyed; imported and protected via prevent_destroy."
  type        = string
  default     = "aegis-public-roots-628478946931"
}

variable "app_param_prefix" {
  description = "Top-level SSM path prefix for app config parameters (no leading slash)."
  type        = string
  default     = "aegis-prodha"
}

variable "audit_kms_alias" {
  description = "KMS alias (sans 'alias/' prefix) for the audit-envelope CMK."
  type        = string
  default     = "aegis-audit-envelope"
}

variable "tags" {
  description = "Default tags applied to every resource via the provider default_tags block."
  type        = map(string)
  default     = {}
}
