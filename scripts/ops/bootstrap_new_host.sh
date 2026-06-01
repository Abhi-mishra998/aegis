#!/usr/bin/env bash
# Bootstrap a fresh EC2 host into the Aegis fleet — sprint-7.5.
#
# Idempotent — re-running is safe. Sets up:
#   - docker + docker compose
#   - aws CLI v2
#   - the CloudWatch Logs agent
#   - the /opt/aegis directory with current code from git
#   - a systemd unit that runs the heartbeat status-page publisher
#
# Pre-conditions:
#   - Amazon Linux 2023 (al2023-ami-*)
#   - EC2 instance role attached (acp-ec2-role) — see infra/terraform/compute.tf
#   - IMDSv2 enforced (per the metadata_options block in compute.tf)
#
# Run as root (sudo). Safe to invoke from user-data on instance launch.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Abhi-mishra998/aegis.git}"
TARGET_DIR="${TARGET_DIR:-/opt/aegis}"
TARGET_BRANCH="${TARGET_BRANCH:-main}"
NON_ROOT_USER="${NON_ROOT_USER:-ec2-user}"

if [ "$EUID" -ne 0 ]; then
  echo "ERROR: must run as root (use sudo)" >&2
  exit 2
fi

echo "=== 1/6 — system packages ==="
dnf -y update
dnf -y install \
  git curl jq tar gzip \
  python3 python3-pip \
  docker

systemctl enable --now docker
usermod -aG docker "$NON_ROOT_USER"

echo "=== 2/6 — docker compose plugin ==="
DOCKER_PLUGIN_DIR=/usr/local/lib/docker/cli-plugins
mkdir -p "$DOCKER_PLUGIN_DIR"
if [ ! -x "$DOCKER_PLUGIN_DIR/docker-compose" ]; then
  COMPOSE_VERSION="v2.27.0"
  curl -fsSL -o "$DOCKER_PLUGIN_DIR/docker-compose" \
    "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-$(uname -m)"
  chmod +x "$DOCKER_PLUGIN_DIR/docker-compose"
fi

echo "=== 3/6 — AWS CLI v2 ==="
if ! command -v aws >/dev/null 2>&1; then
  cd /tmp
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o aws.zip
  dnf -y install unzip
  unzip -q aws.zip
  ./aws/install
  rm -rf aws aws.zip
fi
aws --version

echo "=== 4/6 — clone or pull the repo ==="
if [ -d "$TARGET_DIR/.git" ]; then
  cd "$TARGET_DIR"
  git fetch origin "$TARGET_BRANCH"
  git reset --hard "origin/$TARGET_BRANCH"
else
  mkdir -p "$(dirname "$TARGET_DIR")"
  git clone --branch "$TARGET_BRANCH" --depth 1 "$REPO_URL" "$TARGET_DIR"
fi
chown -R "$NON_ROOT_USER:$NON_ROOT_USER" "$TARGET_DIR"

echo "=== 5/6 — CloudWatch Logs agent ==="
if [ -x "$TARGET_DIR/infra/cloudwatch/install.sh" ]; then
  bash "$TARGET_DIR/infra/cloudwatch/install.sh"
else
  echo "WARN: $TARGET_DIR/infra/cloudwatch/install.sh missing — skipping"
fi

echo "=== 6/6 — systemd: aegis-statuspage publisher ==="
# Runs every 60 seconds via systemd timer (more reliable than GitHub
# Actions' 5-minute cron floor; the GH workflow stays as the SaaS-side
# safety net).
cat > /etc/systemd/system/aegis-statuspage.service <<'UNIT'
[Unit]
Description=Aegis customer-facing status-page snapshot publisher
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/aegis
EnvironmentFile=/opt/aegis/infra/.env
ExecStart=/usr/bin/docker compose -f infra/docker-compose.yml run --rm \
    -e GATEWAY_URL=http://gateway:8000 \
    -e PROMETHEUS_URL=http://prometheus:9090 \
    -e STATUS_S3_BUCKET=aegis-statuspage \
    audit \
    python /app/scripts/maintenance/publish_status_page.py
UNIT

cat > /etc/systemd/system/aegis-statuspage.timer <<'UNIT'
[Unit]
Description=Aegis status-page snapshot every 60 seconds
Requires=aegis-statuspage.service

[Timer]
OnBootSec=2min
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable --now aegis-statuspage.timer

echo ""
echo "=== bootstrap complete ==="
echo "Next steps for the operator:"
echo "  1. aws s3 cp s3://acp-backups-prod-am/config/.env $TARGET_DIR/infra/.env"
echo "  2. aws s3 cp s3://acp-backups-prod-am/config/pgbouncer.aws.ini $TARGET_DIR/infra/pgbouncer.aws.ini"
echo "  3. aws s3 cp s3://acp-backups-prod-am/config/userlist.txt $TARGET_DIR/infra/userlist.txt"
echo "  4. cd $TARGET_DIR && docker compose -f infra/docker-compose.yml up -d --build"
echo "  5. Run scripts/ops/smoke_test.sh to verify."
echo "  6. Register the instance with the ALB target group via Terraform or the"
echo "     scripts/ops/deploy_staggered.sh helper."
