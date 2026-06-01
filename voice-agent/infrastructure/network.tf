# Use the default VPC in the region instead of building a custom one.
# Custom networking is unnecessary for a single public EC2 portfolio demo.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "default-for-az"
    values = ["true"]
  }
}

# Pick the first default subnet in the region — deterministic across plans.
locals {
  subnet_id = sort(data.aws_subnets.default.ids)[0]
}

resource "aws_security_group" "agent" {
  name        = "${local.name_prefix}-sg"
  description = "Inbound SSH from admin IP only; outbound all (LiveKit, Deepgram, Cartesia, Groq, etc.)."
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_ingress_rule" "ssh_from_admin" {
  security_group_id = aws_security_group.agent.id
  description       = "SSH from the admin IP only."
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.admin_ip_cidr
}

resource "aws_vpc_security_group_egress_rule" "all_out_v4" {
  security_group_id = aws_security_group.agent.id
  description       = "Outbound to LiveKit Cloud, Deepgram, Cartesia, Groq, AWS APIs."
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
