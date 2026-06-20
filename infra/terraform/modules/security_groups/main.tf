# 4 security groups composing the zero-trust network policy:
#
#   alb   ingress: 80 + 443 from 0.0.0.0/0
#         egress:  all to ec2_sg only
#   ec2   ingress: 8000 from alb_sg only (gateway port)
#         egress:  all (need NAT for AWS APIs + Anthropic + npm)
#   rds   ingress: 5432 from ec2_sg only
#         egress:  none
#   redis ingress: 6379 from ec2_sg only
#         egress:  none

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb-sg"
  description = "ALB - public 80/443 in; egress to EC2 only."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-alb-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere (redirected to HTTPS at the listener)."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere."
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_ec2" {
  security_group_id            = aws_security_group.alb.id
  description                  = "ALB to EC2 gateway port only."
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 5173
  to_port                      = 5173
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "ec2" {
  name        = "${var.name_prefix}-ec2-sg"
  description = "EC2 - gateway port from ALB; egress for AWS APIs + upstream LLM + npm."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-ec2-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "ec2_from_alb" {
  security_group_id            = aws_security_group.ec2.id
  description                  = "Gateway port 8000 from ALB only."
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = 5173
  to_port                      = 5173
  ip_protocol                  = "tcp"
}

# Egress all - EC2 needs to reach SSM, S3, Secrets Manager, Anthropic,
# OpenAI, npm.  Filtered upstream by NAT + WAF on inbound, not by SG egress.
resource "aws_vpc_security_group_egress_rule" "ec2_all" {
  security_group_id = aws_security_group.ec2.id
  description       = "All egress for AWS APIs + upstream LLMs + npm."
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds-sg"
  description = "RDS - Postgres port from EC2 only; no egress."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-rds-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ec2" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Postgres 5432 from EC2 only."
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 5432
  to_port                      = 5432
  ip_protocol                  = "tcp"
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis-sg"
  description = "Redis - TLS port from EC2 only; no egress."
  vpc_id      = var.vpc_id

  tags = {
    Name = "${var.name_prefix}-redis-sg"
  }
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_ec2" {
  security_group_id            = aws_security_group.redis.id
  description                  = "Redis 6379 from EC2 only."
  referenced_security_group_id = aws_security_group.ec2.id
  from_port                    = 6379
  to_port                      = 6379
  ip_protocol                  = "tcp"
}
