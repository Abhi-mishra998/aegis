# Sprint 9 — Application autoscaling group.
#
# A launch template + ASG + target-group attachment + a couple of
# CloudWatch alarms. We DON'T own the ALB itself — that's the alb module
# — but we do own the target group attachment so a new instance lands
# in service when the ASG scales out.

resource "aws_launch_template" "this" {
  name_prefix   = "${var.name_prefix}-lt-"
  image_id      = var.ami_id
  instance_type = var.instance_type
  key_name      = var.key_name

  vpc_security_group_ids = var.vpc_security_group_ids

  iam_instance_profile {
    name = var.iam_instance_profile_name
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = var.root_volume_size_gb
      volume_type           = "gp3"
      delete_on_termination = true
      encrypted             = true
      kms_key_id            = var.root_volume_kms_key_id
    }
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 — locks down the metadata service
    http_endpoint = "enabled"
    # hop_limit=2 lets containers (1 hop via the docker bridge) reach
    # IMDSv2. hop_limit=1 broke boto3 → SSM → instance-role credential
    # lookup, surfacing as audit /receipts/key NoCredentialsError 500.
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  monitoring {
    enabled = true
  }

  user_data = var.user_data != "" ? base64encode(var.user_data) : null

  tag_specifications {
    resource_type = "instance"
    tags = merge(var.tags, {
      Name = "${var.name_prefix}-asg-instance"
    })
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "this" {
  name_prefix         = "${var.name_prefix}-asg-"
  vpc_zone_identifier = var.subnet_ids
  min_size            = var.min_size
  desired_capacity    = var.desired_capacity
  max_size            = var.max_size

  health_check_type         = "ELB"
  health_check_grace_period = var.health_check_grace_period_seconds
  default_cooldown          = 60

  target_group_arns = [var.alb_target_group_arn]

  launch_template {
    id      = aws_launch_template.this.id
    version = aws_launch_template.this.latest_version
  }

  # Instance refresh — Sprint 9 contract: an LT change triggers a
  # rolling replacement honoring min_healthy=90% so capacity never
  # dips below N+1.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 90
      instance_warmup        = var.health_check_grace_period_seconds
    }
  }

  dynamic "tag" {
    for_each = merge(var.tags, {
      Name = "${var.name_prefix}-asg"
    })
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  lifecycle {
    create_before_destroy = true
    ignore_changes        = [desired_capacity] # don't fight the scaling policy
  }
}

# Target-tracking scaling — keeps the average CPU around 50%. Plenty of
# headroom for a burst while not wasting money on idle instances.
resource "aws_autoscaling_policy" "cpu" {
  name                   = "${var.name_prefix}-cpu-target"
  autoscaling_group_name = aws_autoscaling_group.this.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value     = 50.0
    disable_scale_in = false
  }
}

# CloudWatch — page on sustained unhealthy host count > 0.
resource "aws_cloudwatch_metric_alarm" "unhealthy_hosts" {
  alarm_name          = "${var.name_prefix}-unhealthy-hosts"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "GroupInServiceInstances"
  namespace           = "AWS/AutoScaling"
  period              = 60
  statistic           = "Average"
  threshold           = max(0, var.min_size - 1)

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.this.name
  }

  alarm_description  = "ASG has fewer than min_size healthy instances for 2 minutes."
  treat_missing_data = "notBreaching"
  tags               = var.tags
}
