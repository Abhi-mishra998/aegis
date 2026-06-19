output "vpc_id" {
  description = "VPC id."
  value       = aws_vpc.main.id
}

output "vpc_cidr_block" {
  description = "VPC CIDR (used by SG rules)."
  value       = aws_vpc.main.cidr_block
}

output "public_subnet_ids" {
  description = "Public subnet ids (ALB + NAT)."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet ids (EC2 + RDS + Redis)."
  value       = aws_subnet.private[*].id
}

output "nat_gateway_ids" {
  description = "NAT Gateway ids (1 when single_nat_gateway = true)."
  value       = aws_nat_gateway.main[*].id
}
