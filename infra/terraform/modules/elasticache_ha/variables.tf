# Sprint 9 — Redis replication group with Multi-AZ + automatic failover.
#
# Wraps aws_elasticache_replication_group with sensible production
# defaults: encryption at rest + in transit, automatic backups,
# automatic failover, Multi-AZ enabled.

variable "name_prefix" {
  description = "Resource name prefix (e.g. acp-prod-ha)"
  type        = string
}

variable "replication_group_id" {
  description = "Replication group identifier (lowercase, max 40 chars)."
  type        = string
}

variable "description" {
  description = "Human description for the replication group."
  type        = string
  default     = "Aegis HA Redis"
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "node_type" {
  description = "Node type — cache.t3.medium is the production floor for the 7-stream workload."
  type        = string
  default     = "cache.t3.medium"
}

variable "num_node_groups" {
  description = "Sharding factor. 1 = primary + replicas. Multi-shard requires N node groups."
  type        = number
  default     = 1
}

variable "replicas_per_node_group" {
  description = "Replicas per shard. Set ≥1 to enable automatic failover; ≥2 for cross-AZ HA."
  type        = number
  default     = 2
}

variable "subnet_ids" {
  description = "PRIVATE subnet ids — at least 2 across distinct AZs."
  type        = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "elasticache_ha needs at least 2 subnets across distinct AZs."
  }
}

variable "security_group_ids" {
  description = "Security groups attached to the replication group."
  type        = list(string)
}

variable "parameter_group_name" {
  description = "Optional parameter group; defaults to the engine-version default."
  type        = string
  default     = null
}

variable "snapshot_retention_limit_days" {
  description = "Daily snapshot retention. 0 disables; 7 is the production floor."
  type        = number
  default     = 7
}

variable "kms_key_id" {
  description = "KMS CMK for encryption at rest. Null falls back to the aws-managed key."
  type        = string
  default     = null
}

variable "auth_token_secret_arn" {
  description = "Optional Secrets Manager ARN holding the AUTH token. When set the module reads the secret_string at plan time."
  type        = string
  default     = null
}

variable "tags" {
  type    = map(string)
  default = {}
}
