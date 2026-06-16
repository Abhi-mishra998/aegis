variable "aws_region" {
  description = "AWS region for HA prod (ap-south-1 = Mumbai)."
  type        = string
  default     = "ap-south-1"
}

variable "route53_zone_id" {
  description = "Hosted zone for aegisagent.in (re-use the existing one from prod/)."
  type        = string
  default     = "Z033117538JKIIKDBDPUJ"
}

variable "bucket_suffix" {
  description = "Suffix appended to globally-unique bucket names — typically the AWS account id."
  type        = string
  default     = "628478946931"
}

variable "ec2_ami_id" {
  description = "AMI for the autoscaled fleet. Resolve via SSM parameter at apply time."
  type        = string
}

variable "ec2_key_name" {
  description = "EC2 keypair (legacy SSH). Leave null in HA prod — operators use SSM Session Manager."
  type        = string
  default     = null
}

variable "ec2_instance_type" {
  description = "ASG node instance type. m6g.large (8 GB RAM) is the floor for real customer traffic — the 22-container stack needs ~5 GB RSS at idle and bursts higher under concurrent /execute load. m6g.medium (4 GB) is over-committed and OOM-kills hot services (behavior/identity/registry/policy) under any concurrent traffic."
  type        = string
  default     = "m6g.large"
}

# 20-user testing infra sizing (revised 2026-06-13 per operator request).
# HA where it matters (Multi-AZ RDS + Redis replication group + ASG across
# 2 AZs) but right-sized for ~20 concurrent reviewers — not the
# 500-concurrent buyer-scale stack the original prod-ha targeted.
#
# To scale up later: bump rds_instance_class to db.t3.medium, redis to
# cache.t3.medium, replicas_per_node_group to 2, asg_max_size to 6.

variable "rds_instance_class" {
  description = "RDS instance class — db.t3.small for 20-user testing infra."
  type        = string
  default     = "db.t3.small"
}

variable "redis_node_type" {
  description = "ElastiCache node type. cache.t3.micro covers 20 users; bump to t3.medium for real load."
  type        = string
  default     = "cache.t3.micro"
}

variable "redis_replicas_per_node_group" {
  description = "Replicas per shard. 1 = primary + 1 cross-AZ replica (cheapest HA)."
  type        = number
  default     = 1
}

variable "asg_min_size" {
  description = "ASG floor — 1 for 20-user infra; bump to 2 for N+1 production HA."
  type        = number
  default     = 1
}

variable "asg_desired_capacity" {
  type    = number
  default = 1
}

variable "asg_max_size" {
  description = "ASG ceiling — 2 covers 20-user burst; bump for real customer load."
  type        = number
  default     = 2
}

variable "waf_rate_limit_per_5min" {
  description = "Per-IP rate limit at the WAF (0 disables)."
  type        = number
  default     = 5000
}

variable "waf_ip_allowlist" {
  description = "Optional allowlist for pen-test windows or vendor IPs."
  type        = list(string)
  default     = []
}
