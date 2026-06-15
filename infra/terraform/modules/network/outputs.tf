output "vpc_id" {
  value = aws_vpc.this.id
}

output "vpc_cidr" {
  value = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "internet_gateway_id" {
  value = aws_internet_gateway.this.id
}

output "availability_zones" {
  value = var.availability_zones
}

output "nat_gateway_public_ips" {
  description = "Per-AZ NAT EIPs — pin in vendor allowlists for egress from private subnets."
  value       = [for eip in aws_eip.nat : eip.public_ip]
}

output "private_route_table_ids" {
  value = aws_route_table.private[*].id
}
