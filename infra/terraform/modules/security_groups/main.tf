# Four security groups in a layered topology:
#   alb  : public 80/443 ingress
#   ec2  : ingress from alb on gateway_port + ui_port; optional SSH
#   rds  : 5432 ingress from ec2 only
#   redis: 6379 ingress from ec2 only

resource "aws_security_group" "alb" {
  name        = "${var.name_prefix}-alb-sg"
  description = "Internet-facing ALB"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP (redirected to HTTPS)"
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-alb-sg" })
}

resource "aws_security_group" "ec2" {
  name        = "${var.name_prefix}-ec2-sg"
  description = "Application EC2 instances"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = var.gateway_port
    to_port         = var.gateway_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "ALB to gateway"
  }
  ingress {
    from_port       = var.ui_port
    to_port         = var.ui_port
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
    description     = "ALB to UI nginx"
  }

  # Optional SSH - only enabled when ssh_allowed_cidrs is non-empty.
  # Prefer SSM Session Manager over SSH where possible.
  dynamic "ingress" {
    for_each = length(var.ssh_allowed_cidrs) > 0 ? [1] : []
    content {
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.ssh_allowed_cidrs
      description = "SSH from operator IPs"
    }
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-ec2-sg" })
}

resource "aws_security_group" "rds" {
  name        = "${var.name_prefix}-rds-sg"
  description = "RDS PostgreSQL - only EC2 may connect"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
    description     = "EC2 to Postgres"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-rds-sg" })
}

resource "aws_security_group" "redis" {
  name        = "${var.name_prefix}-redis-sg"
  description = "ElastiCache Redis - only EC2 may connect"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ec2.id]
    description     = "EC2 to Redis"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-redis-sg" })
}
