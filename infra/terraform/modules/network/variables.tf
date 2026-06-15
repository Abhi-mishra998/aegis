variable "name_prefix" {
  description = "Resource name prefix (e.g. acp-dev, acp-prod)"
  type        = string
}

variable "vpc_cidr" {
  description = "Primary VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "availability_zones" {
  description = "AZs to span — first N are used. At least 2 required for Multi-AZ RDS."
  type        = list(string)
  validation {
    condition     = length(var.availability_zones) >= 2
    error_message = "Need at least 2 AZs (RDS Multi-AZ + ALB cross-AZ)."
  }
}

variable "public_subnet_cidrs" {
  description = "Public subnet CIDRs, one per AZ in availability_zones order"
  type        = list(string)
  default     = ["10.0.1.0/24", "10.0.2.0/24"]
}

variable "private_subnet_cidrs" {
  description = "Private subnet CIDRs, one per AZ"
  type        = list(string)
  default     = ["10.0.3.0/24", "10.0.4.0/24"]
}

variable "tags" {
  description = "Extra tags merged onto every resource"
  type        = map(string)
  default     = {}
}

# Sprint 9 — Opt-in NAT gateways for HA prod.
#
# Default is false so the existing dev + prod environments don't pay for
# NAT they don't use. Set to true in the prod-ha environment so the
# autoscaled app tier in PRIVATE subnets can reach AWS APIs (KMS, SSM,
# Secrets Manager) and pull container images.
variable "enable_nat_gateways" {
  description = "Provision one NAT gateway per AZ for the private subnets' default route."
  type        = bool
  default     = false
}

# Per-AZ NAT is the audit-friendly default — a single shared NAT is a
# blast radius for a single AZ failure. Operators who want to pay
# less for non-prod can flip this off and accept the SPOF.
variable "one_nat_per_az" {
  description = "When enable_nat_gateways is true: one NAT per AZ vs a single shared NAT in the first AZ."
  type        = bool
  default     = true
}
