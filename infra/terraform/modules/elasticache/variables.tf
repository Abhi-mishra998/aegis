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
