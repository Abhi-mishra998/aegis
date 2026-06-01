# EC2 t3.medium running Ubuntu 24.04 LTS. The cloud-init user_data:
#   - installs python3 + venv + git + awscli
#   - clones the repo
#   - pulls runtime secrets from Secrets Manager into /opt/aegis/agent/.env.local
#   - creates the venv, installs Python deps
#   - downloads the embedding + reranker models so first-call latency is low
#   - runs ingest.py to build chroma_db + bm25_index on the EBS volume
#   - installs and starts the aegis-agent.service systemd unit

data "aws_ami" "ubuntu_2404" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    aws_region   = var.aws_region
    project_name = var.project_name
    repo_url     = "https://github.com/your-org/voice-agent.git" # placeholder; user can override
  })
}

resource "aws_instance" "agent" {
  ami                    = data.aws_ami.ubuntu_2404.id
  instance_type          = var.instance_type
  subnet_id              = local.subnet_id
  vpc_security_group_ids = [aws_security_group.agent.id]
  key_name               = aws_key_pair.agent.key_name
  iam_instance_profile   = aws_iam_instance_profile.agent.name

  user_data                   = local.user_data
  user_data_replace_on_change = true

  monitoring                           = true
  ebs_optimized                        = true
  instance_initiated_shutdown_behavior = "stop"

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only — prevents SSRF stealing creds
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gb
    encrypted             = true
    delete_on_termination = true
    tags = {
      Name = "${local.name_prefix}-root"
    }
  }

  tags = {
    Name = local.name_prefix
  }
}

resource "aws_eip" "agent" {
  instance = aws_instance.agent.id
  domain   = "vpc"

  tags = {
    Name = "${local.name_prefix}-eip"
  }
}
