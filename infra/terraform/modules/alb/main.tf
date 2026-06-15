# Internet-facing ALB with HTTPS listener + HTTP→HTTPS redirect.
# Target group health checks the application's /health endpoint.

resource "aws_lb" "this" {
  name               = var.alb_name
  internal           = false
  load_balancer_type = "application"

  subnets         = var.subnet_ids
  security_groups = var.security_group_ids

  enable_deletion_protection = var.enable_deletion_protection
  enable_http2               = true
  idle_timeout               = 65
  drop_invalid_header_fields = true

  dynamic "access_logs" {
    for_each = length(var.access_logs_bucket) > 0 ? [1] : []
    content {
      bucket  = var.access_logs_bucket
      prefix  = "alb"
      enabled = true
    }
  }

  tags = merge(var.tags, { Name = var.alb_name })
}

resource "aws_lb_target_group" "this" {
  name        = "${var.name_prefix}-tg"
  port        = var.target_port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "instance"

  health_check {
    path                = var.health_check_path
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  # Match scripts/ops/deploy_staggered.sh DRAIN_WAIT_SECONDS=60 — ALB
  # waits 60s for in-flight requests to drain before forcibly closing.
  deregistration_delay = 60

  tags = merge(var.tags, { Name = "${var.name_prefix}-tg" })
}

resource "aws_lb_target_group_attachment" "instances" {
  count            = length(var.target_instance_ids)
  target_group_arn = aws_lb_target_group.this.arn
  target_id        = var.target_instance_ids[count.index]
  port             = var.target_port
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      protocol    = "HTTPS"
      port        = "443"
      status_code = "HTTP_301"
    }
  }
}
