# Launch Template + ASG + target-tracking scaling policy.
#
# user_data:
#   1. Read /aegis/prod/current_bundle_sha from SSM Parameter.
#   2. Pull bundle-<sha>.tar.gz from S3 bundle bucket.
#   3. Extract to /opt/aegis.
#   4. Run docker compose up -d.
#
# Instance refresh policy: MinHealthyPercentage = 100 — ASG cannot
# terminate a healthy old instance until the new one passes ALB health
# checks. This is the permanent fix for the 2026-06-18 outage where
# current.tar.gz overwrites cascaded ASG into killing the last healthy
# instance.

# Latest Amazon Linux 2023 arm64 AMI.
data "aws_ami" "al2023_arm64" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-arm64"]
  }
  filter {
    name   = "architecture"
    values = ["arm64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail

    # Install Docker + Docker Compose plugin if missing.
    dnf install -y docker
    systemctl enable --now docker

    # The compose plugin ships in AL2023 by default; verify.
    docker compose version || dnf install -y docker-compose-plugin

    BUNDLE_SHA=$(aws ssm get-parameter \
      --region ${var.aws_region} \
      --name ${var.ssm_bundle_parameter} \
      --query 'Parameter.Value' --output text)

    aws s3 cp \
      s3://${var.bundle_bucket}/releases/bundle-$${BUNDLE_SHA}.tar.gz \
      /tmp/bundle.tar.gz
    mkdir -p /opt/aegis
    tar -xzf /tmp/bundle.tar.gz -C /opt/aegis
    cd /opt/aegis

    # docker-compose.aws.yml carries the prod overlay (RDS endpoint,
    # Redis endpoint, secret ARNs). Ship in the bundle, NOT baked into
    # the AMI — that way overlays update with the bundle.
    docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d
  EOT
}

resource "aws_launch_template" "main" {
  name_prefix   = "${var.name_prefix}-lt-"
  image_id      = data.aws_ami.al2023_arm64.id
  instance_type = var.instance_type

  iam_instance_profile {
    name = var.instance_profile
  }

  vpc_security_group_ids = [var.ec2_security_group]

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 2
    http_endpoint               = "enabled"
  }

  monitoring {
    enabled = true
  }

  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 30
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  user_data = base64encode(local.user_data)

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.name_prefix}-ec2"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "${var.name_prefix}-ec2-root"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "main" {
  name                = "${var.name_prefix}-asg"
  vpc_zone_identifier = var.private_subnet_ids
  min_size            = var.asg_min
  max_size            = var.asg_max
  desired_capacity    = var.asg_desired

  health_check_type         = "ELB"
  health_check_grace_period = 300

  target_group_arns = [var.target_group_arn]

  launch_template {
    id      = aws_launch_template.main.id
    version = "$Latest"
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 100
      instance_warmup        = 300
    }
    # `launch_template` is always an implied trigger — no need to list it.
  }

  tag {
    key                 = "Name"
    value               = "${var.name_prefix}-ec2"
    propagate_at_launch = true
  }

  # Bundle-SHA changes are pushed via SSM, not by terraform — so don't
  # let desired_capacity diff during routine reads.
  lifecycle {
    ignore_changes        = [desired_capacity]
    create_before_destroy = true
  }
}

# Target-tracking scaling — 60% CPU avg over the ASG. Headroom big
# enough that a 100rps spike (4x sustained) doesn't trigger; tight
# enough that a sustained climb does.
resource "aws_autoscaling_policy" "cpu_target" {
  name                   = "${var.name_prefix}-asg-cpu-target"
  autoscaling_group_name = aws_autoscaling_group.main.name
  policy_type            = "TargetTrackingScaling"

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value = 60.0
  }
}
