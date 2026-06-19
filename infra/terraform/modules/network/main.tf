# VPC + 2 public + 2 private subnets + IGW + ONE NAT Gateway + route tables
# + S3 VPC gateway endpoint (free, skips NAT for S3 traffic).
#
# ALB requires public subnets in two AZs — we get that requirement met.
# Compute (EC2 + RDS + Redis) lives entirely in private subnets.

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name_prefix}-vpc"
  }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${var.name_prefix}-igw"
  }
}

resource "aws_subnet" "public" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.azs[count.index]
  map_public_ip_on_launch = false # ALB lives here; nothing else gets a public IP

  tags = {
    Name = "${var.name_prefix}-public-${var.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.azs[count.index]

  tags = {
    Name = "${var.name_prefix}-private-${var.azs[count.index]}"
    Tier = "private"
  }
}

# Single NAT Gateway in the first public subnet. 1a outage = no outbound
# until we manually add a second NAT or fail over. Saves ~$33/mo.
resource "aws_eip" "nat" {
  count  = var.single_nat_gateway ? 1 : length(var.azs)
  domain = "vpc"

  tags = {
    Name = "${var.name_prefix}-eip-nat-${count.index}"
  }
}

resource "aws_nat_gateway" "main" {
  count         = var.single_nat_gateway ? 1 : length(var.azs)
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${var.name_prefix}-nat-${count.index}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${var.name_prefix}-rt-public"
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# One private route table per AZ. When single_nat_gateway = true, both
# private RTs point at the single NAT; that means a 1a outage knocks
# 1b's outbound too. Accept it at design-partner stage.
resource "aws_route_table" "private" {
  count  = length(var.azs)
  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[var.single_nat_gateway ? 0 : count.index].id
  }

  tags = {
    Name = "${var.name_prefix}-rt-private-${var.azs[count.index]}"
  }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# S3 VPC gateway endpoint — free, and stops S3 traffic going through NAT
# (NAT bytes are billed; gateway endpoint isn't). Critical for the bundle
# download path used by user_data.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.public.id])

  tags = {
    Name = "${var.name_prefix}-vpce-s3"
  }
}

# DynamoDB VPC gateway endpoint — free. Aegis itself doesn't currently use
# DynamoDB, but the endpoint costs nothing to keep and lets future locking /
# rate-limit / billing-counters move to DDB without re-plumbing the network.
# The brownfield account had this endpoint manually added (vpce-0140b9f7e623f36b4);
# we keep parity.
resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = concat(aws_route_table.private[*].id, [aws_route_table.public.id])

  tags = {
    Name = "${var.name_prefix}-vpce-dynamodb"
  }
}

data "aws_region" "current" {}
