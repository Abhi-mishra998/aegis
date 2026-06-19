variable "name_prefix" {
  description = "Project-environment naming prefix."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR block."
  type        = string
}

variable "azs" {
  description = "Two AZs."
  type        = list(string)
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDRs (ALB + NAT)."
  type        = list(string)
}

variable "private_subnet_cidrs" {
  description = "Two private subnet CIDRs (EC2 + RDS + Redis)."
  type        = list(string)
}

variable "single_nat_gateway" {
  description = "True for one shared NAT (saves ~$33/mo, single AZ failure point)."
  type        = bool
  default     = true
}
