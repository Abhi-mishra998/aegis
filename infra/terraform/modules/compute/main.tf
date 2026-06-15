# EC2 instances — one role for the whole fleet, spread round-robin across
# the supplied subnets. Each instance gets:
#   - Latest Amazon Linux 2023 AMI for the region
#   - IMDSv2 required (prevents the SSRF-against-metadata pattern the
#     sprint-1 webhook executor fix guards against in code)
#   - GP3 root EBS, encrypted, delete-on-termination
#   - User-data that runs `scripts/ops/bootstrap_new_host.sh` by default

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-${var.architecture}"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "architecture"
    values = [var.architecture]
  }
}

resource "aws_instance" "app" {
  count = var.instance_count

  ami                    = data.aws_ami.al2023.id
  instance_type          = var.instance_type
  subnet_id              = element(var.subnet_ids, count.index)
  vpc_security_group_ids = var.vpc_security_group_ids
  iam_instance_profile   = var.iam_instance_profile_name
  key_name               = length(var.key_name) > 0 ? var.key_name : null

  monitoring    = true # 1-minute CloudWatch metrics
  ebs_optimized = true

  user_data = length(var.user_data) > 0 ? var.user_data : null

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    encrypted             = true
    delete_on_termination = true
  }

  metadata_options {
    http_tokens   = "required" # IMDSv2 mandatory
    http_endpoint = "enabled"
    # hop_limit=2 so docker containers (1 hop via the bridge interface)
    # can reach IMDSv2 — required for boto3 → SSM → instance-role creds.
    http_put_response_hop_limit = 2
  }

  tags = merge(var.tags, {
    Name = "${var.name_prefix}-app-${count.index + 1}"
  })

  lifecycle {
    # Don't recreate on AMI changes — operators upgrade in place via SSM.
    ignore_changes = [ami, user_data]
  }
}
