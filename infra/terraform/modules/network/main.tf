# VPC + public + private subnets across the supplied AZs, with an IGW and
# public route table. Private subnets do NOT have a NAT gateway by default
# (the dev environment doesn't need outbound from private subnets; prod
# may opt to add one — see modules/network/nat.tf in a future addition).

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-vpc"
  })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags = merge(var.tags, {
    Name = "${var.name_prefix}-igw"
  })
}

# Public subnets — one per AZ. Index-aligned with availability_zones.
resource "aws_subnet" "public" {
  count                   = length(var.availability_zones)
  vpc_id                  = aws_vpc.this.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-${substr(var.availability_zones[count.index], -2, 2)}"
    Tier = "public"
  })
}

# Private subnets — same AZ mapping; for RDS + Redis.
resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-private-${substr(var.availability_zones[count.index], -2, 2)}"
    Tier = "private"
  })
}

# Public route table — default route through IGW; associated to public subnets.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = merge(var.tags, {
    Name = "${var.name_prefix}-public-rt"
  })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# Private route tables — one per AZ when NAT is enabled (per-AZ NAT is
# the audit-friendly default). When NAT is disabled there's a single
# private RT shared by every private subnet — matches the pre-Sprint-9
# behaviour exactly.
locals {
  private_rt_count = (var.enable_nat_gateways && var.one_nat_per_az) ? length(var.availability_zones) : 1
  nat_count        = var.enable_nat_gateways ? (var.one_nat_per_az ? length(var.availability_zones) : 1) : 0
}

resource "aws_route_table" "private" {
  count  = local.private_rt_count
  vpc_id = aws_vpc.this.id
  tags = merge(var.tags, {
    Name = local.private_rt_count == 1 ? "${var.name_prefix}-private-rt" : "${var.name_prefix}-private-rt-${substr(var.availability_zones[count.index], -2, 2)}"
  })
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = local.private_rt_count == 1 ? aws_route_table.private[0].id : aws_route_table.private[count.index].id
}

# ──────────────────────────────────────────────────────────────────────
# Sprint 9 — Opt-in NAT gateways. Disabled in dev + existing prod (no
# changes); enabled in prod-ha. Each NAT gets an Elastic IP so an
# operator can pin egress IPs in vendor allowlists.
# ──────────────────────────────────────────────────────────────────────

resource "aws_eip" "nat" {
  count  = local.nat_count
  domain = "vpc"

  tags = merge(var.tags, {
    Name = local.nat_count == 1 ? "${var.name_prefix}-nat-eip" : "${var.name_prefix}-nat-eip-${substr(var.availability_zones[count.index], -2, 2)}"
  })
}

resource "aws_nat_gateway" "this" {
  count         = local.nat_count
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = merge(var.tags, {
    Name = local.nat_count == 1 ? "${var.name_prefix}-nat" : "${var.name_prefix}-nat-${substr(var.availability_zones[count.index], -2, 2)}"
  })

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route" "private_default" {
  count                  = local.nat_count
  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[count.index].id
}
