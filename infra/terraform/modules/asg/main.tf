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
  user_data = <<EOT
#!/bin/bash
set -euo pipefail
exec > >(tee -a /var/log/aegis-boot.log) 2>&1

REGION="${var.aws_region}"
PARAM_PREFIX="${var.app_param_prefix}"

dnf install -y docker postgresql15
systemctl enable --now docker

DOCKER_PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
mkdir -p "$DOCKER_PLUGIN_DIR"
if ! docker compose version >/dev/null 2>&1; then
  COMPOSE_VERSION="v2.27.0"
  curl -fsSL -o "$DOCKER_PLUGIN_DIR/docker-compose" \
    "https://github.com/docker/compose/releases/download/$${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)"
  chmod +x "$DOCKER_PLUGIN_DIR/docker-compose"
fi
docker compose version

BUNDLE_SHA=$(aws ssm get-parameter --region "$REGION" --name ${var.ssm_bundle_parameter} --query 'Parameter.Value' --output text)
aws s3 cp s3://${var.bundle_bucket}/releases/bundle-$${BUNDLE_SHA}.tar.gz /tmp/bundle.tar.gz

# Sprint EH-4 — cryptographic chain. If the bundle is accompanied by a
# cosign signature + cert + sigstore bundle, verify before extracting.
# If verification fails OR the artefacts are missing AND the SSM gate
# parameter aegis/prod/require_signed_bundle == "true", abort the boot.
REQUIRE_SIGNED=$(aws ssm get-parameter --region "$REGION" --name /aegis/prod/require_signed_bundle --query 'Parameter.Value' --output text 2>/dev/null || echo "false")
if aws s3 cp s3://${var.bundle_bucket}/releases/bundle-$${BUNDLE_SHA}.tar.gz.sig /tmp/bundle.tar.gz.sig 2>/dev/null \
   && aws s3 cp s3://${var.bundle_bucket}/releases/bundle-$${BUNDLE_SHA}.tar.gz.pem /tmp/bundle.tar.gz.pem 2>/dev/null \
   && aws s3 cp s3://${var.bundle_bucket}/releases/bundle-$${BUNDLE_SHA}.tar.gz.bundle /tmp/bundle.tar.gz.bundle 2>/dev/null; then
  # Pull cosign once.
  if ! command -v cosign >/dev/null 2>&1; then
    curl -fsSL -o /usr/local/bin/cosign \
      "https://github.com/sigstore/cosign/releases/latest/download/cosign-linux-$(uname -m | sed 's|aarch64|arm64|;s|x86_64|amd64|')"
    chmod +x /usr/local/bin/cosign
  fi
  if cosign verify-blob \
      --certificate     /tmp/bundle.tar.gz.pem \
      --signature       /tmp/bundle.tar.gz.sig \
      --bundle          /tmp/bundle.tar.gz.bundle \
      --certificate-identity-regexp '^https://github\.com/Abhi-mishra998/aegis/' \
      --certificate-oidc-issuer     'https://token.actions.githubusercontent.com' \
      /tmp/bundle.tar.gz; then
    echo "[boot] cosign verify-blob PASSED — proceeding to extract"
  else
    echo "[boot] cosign verify-blob FAILED" >&2
    if [ "$REQUIRE_SIGNED" = "true" ]; then
      echo "[boot] require_signed_bundle=true — aborting deploy" >&2
      exit 4
    fi
    echo "[boot] require_signed_bundle=false — proceeding anyway (will be enforced post-rollout)" >&2
  fi
elif [ "$REQUIRE_SIGNED" = "true" ]; then
  echo "[boot] signed-bundle artefacts missing AND require_signed_bundle=true — abort" >&2
  exit 5
fi

mkdir -p /opt/aegis
tar -xzf /tmp/bundle.tar.gz -C /opt/aegis
cd /opt/aegis

ssm() { aws ssm get-parameter --region "$REGION" --name "$1" --with-decryption --query Parameter.Value --output text 2>/dev/null || echo ""; }
sec() { aws secretsmanager get-secret-value --region "$REGION" --secret-id "$1" --query SecretString --output text 2>/dev/null || echo ""; }

DB_PASS=$(sec "${var.rds_master_secret_id}")
INT_SEC=$(sec "${var.internal_secret_arn}")
JWT_SECRET=$(sec "${var.jwt_signing_secret_id}")
MESH_JWT=$(sec "${var.mesh_jwt_secret_id}")
STRIPE_WEBHOOK=$(sec "${var.stripe_webhook_secret_id}")
GROQ_KEY=$(sec "${var.groq_api_key_secret_id}")

ANTHROPIC_KEY=$(ssm "/$${PARAM_PREFIX}/anthropic/upstream-key")
AUTH_PROVIDER=$(ssm "/$${PARAM_PREFIX}/aegis/auth-provider")
CLERK_SECRET=$(ssm "/$${PARAM_PREFIX}/clerk/secret-key")
CLERK_PUB=$(ssm "/$${PARAM_PREFIX}/clerk/publishable-key")
CLERK_FRONT=$(ssm "/$${PARAM_PREFIX}/clerk/frontend-api")
CLERK_ISSUER=$(ssm "/$${PARAM_PREFIX}/clerk/issuer")
CLERK_JWKS=$(ssm "/$${PARAM_PREFIX}/clerk/jwks-url")
CLERK_TMPL=$(ssm "/$${PARAM_PREFIX}/clerk/jwt-template")
CLERK_WEBHOOK=$(ssm "/$${PARAM_PREFIX}/clerk/webhook-secret")
STRIPE_KEY=$(ssm "/$${PARAM_PREFIX}/stripe/secret-key")
STRIPE_PRO=$(ssm "/$${PARAM_PREFIX}/stripe/pro-price-id")
STRIPE_ENT=$(ssm "/$${PARAM_PREFIX}/stripe/enterprise-price-id")

RDS_HOST="${var.rds_endpoint}"
REDIS_HOST="${var.redis_primary_endpoint}"
DOMAIN="${var.domain}"
RDS_HOST_ONLY=$${RDS_HOST%%:*}

# Grafana admin password - per-host, stashed in SSM so subsequent boots reuse it.
GRAFANA_PWD=$(ssm "/$${PARAM_PREFIX}/grafana/admin-password")
case "$${GRAFANA_PWD}" in
  ""|PLACEHOLDER*)
    GRAFANA_PWD=$(openssl rand -hex 16)
    aws ssm put-parameter --region "$REGION" --name "/$${PARAM_PREFIX}/grafana/admin-password" --value "$${GRAFANA_PWD}" --type SecureString --overwrite >/dev/null 2>&1 || true
    ;;
esac

umask 077
cat > /opt/aegis/infra/.env <<ENV
DATABASE_URL=postgresql+asyncpg://aegis:$${DB_PASS}@$${RDS_HOST}/aegis?ssl=require
REDIS_URL=rediss://$${REDIS_HOST}/0
JWT_SECRET_KEY=$${JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_EXPIRY_MINUTES=15
INTERNAL_SECRET=$${INT_SEC}
MESH_JWT_SECRET=$${MESH_JWT}
OPA_URL=http://opa:8181
OPA_FAIL_MODE=closed
ANTHROPIC_API_KEY=$${ANTHROPIC_KEY}
GROQ_API_KEY=$${GROQ_KEY}
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_MODEL_FAST=llama-3.1-8b-instant
ACP_AUTH_PROVIDER=$${AUTH_PROVIDER}
CLERK_SECRET_KEY=$${CLERK_SECRET}
CLERK_PUBLISHABLE_KEY=$${CLERK_PUB}
CLERK_FRONTEND_API=$${CLERK_FRONT}
CLERK_ISSUER=$${CLERK_ISSUER}
CLERK_JWKS_URL=$${CLERK_JWKS}
CLERK_JWT_TEMPLATE=$${CLERK_TMPL}
CLERK_WEBHOOK_SECRET=$${CLERK_WEBHOOK}
STRIPE_SECRET_KEY=$${STRIPE_KEY}
STRIPE_WEBHOOK_SECRET=$${STRIPE_WEBHOOK}
STRIPE_PRO_PRICE_ID=$${STRIPE_PRO}
STRIPE_ENTERPRISE_PRICE_ID=$${STRIPE_ENT}
PUBLIC_BASE_URL=https://$${DOMAIN}
INTERNAL_GATEWAY_URL=http://gateway:8000
ENVIRONMENT=production
LOG_LEVEL=INFO
ALLOWED_ORIGINS=https://$${DOMAIN},https://www.$${DOMAIN}
RECEIPT_SIGNING_PROVIDER=ssm
RECEIPT_SIGNING_SSM_PARAMETER=/$${PARAM_PREFIX}/receipt-signing-key
AWS_REGION=$${REGION}
PUBLIC_ROOTS_BUCKET=${var.public_roots_bucket}
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=$${GRAFANA_PWD}
VITE_GATEWAY_URL=https://$${DOMAIN}
SLACK_OAUTH_CLIENT_ID=
SLACK_OAUTH_CLIENT_SECRET=
SLACK_WEBHOOK_URL=
PAGERDUTY_ROUTING_KEY=
ENV

chmod 600 /opt/aegis/infra/.env
chown root:root /opt/aegis/infra/.env

if [ -f /opt/aegis/infra/pgbouncer.aws.ini ]; then
  sed -i "s|host=[^ ]*\.rds\.amazonaws\.com|host=$${RDS_HOST_ONLY}|g" /opt/aegis/infra/pgbouncer.aws.ini
fi

export PGPASSWORD="$${DB_PASS}"
for db in acp_registry acp_identity acp_audit acp_api acp_usage acp_identity_graph acp_flight_recorder acp_autonomy acp_behavior; do
  psql -h "$${RDS_HOST_ONLY}" -U aegis -d aegis -v ON_ERROR_STOP=0 -tc "SELECT 1 FROM pg_database WHERE datname='$${db}'" | grep -q 1 \
    || psql -h "$${RDS_HOST_ONLY}" -U aegis -d aegis -v ON_ERROR_STOP=0 -c "CREATE DATABASE $${db}"
done
psql -h "$${RDS_HOST_ONLY}" -U aegis -d aegis -v ON_ERROR_STOP=0 -f /opt/aegis/infra/init-db.sql || true
unset PGPASSWORD

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
  health_check_grace_period = 1200

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
