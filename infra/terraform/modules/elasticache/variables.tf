variable "name_prefix" {
  type = string
}

variable "cluster_id" {
  description = "ElastiCache cluster ID (lowercase, no underscores)"
  type        = string
}

variable "engine_version" {
  type    = string
  default = "7.1"
}

variable "node_type" {
  type    = string
  default = "cache.t3.micro"
}

variable "num_cache_nodes" {
  description = "Single-node = 1 (dev). Multi-AZ replication group requires num_cache_clusters >= 2 via the replication_group resource (TODO if needed)."
  type        = number
  default     = 1
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_ids" {
  type = list(string)
}

variable "snapshot_retention_limit_days" {
  description = "Days of snapshots to retain. 0 disables snapshots (dev). 7 for prod."
  type        = number
  default     = 0
}

variable "tags" {
  type    = map(string)
  default = {}
}
