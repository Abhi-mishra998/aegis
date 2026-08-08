variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "vpc_id" {
  description = "VPC id (informational)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet ids for the cache subnet group."
  type        = list(string)
}

variable "redis_security_group" {
  description = "Redis security group id."
  type        = string
}

variable "node_type" {
  description = "ElastiCache node type."
  type        = string
  default     = "cache.t3.micro"
}

variable "num_nodes" {
  description = "Total nodes in the replication group (1 primary + N-1 replicas)."
  type        = number
  default     = 2
}

variable "redis_auth_token" {
  description = "Redis AUTH token (fix M6). Wired from module.secrets.redis_auth_token.result. ASG user_data must template this into infra/.env as REDIS_AUTH_TOKEN and include it in REDIS_URL as rediss://:<token>@host:6379/0."
  type        = string
  sensitive   = true
}
