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
