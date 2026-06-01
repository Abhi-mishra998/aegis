#!/usr/bin/env bash
# Install + start the AWS CloudWatch Agent on an EC2 host — sprint-4.D.
#
# Pre-requisites (on the host's IAM role):
#   - logs:CreateLogGroup, logs:CreateLogStream, logs:PutLogEvents, logs:DescribeLogStreams
#   - cloudwatch:PutMetricData (used by the agent for self-metrics)
#
# Idempotent — re-running is safe. Restart the agent if config changes.

set -euo pipefail

INSTANCE_TYPE=$(uname -m)
case "$INSTANCE_TYPE" in
  x86_64)  ARCH="amd64" ;;
  aarch64) ARCH="arm64" ;;
  *)       echo "Unsupported arch $INSTANCE_TYPE"; exit 2 ;;
esac

PKG_URL="https://s3.amazonaws.com/amazoncloudwatch-agent/amazon_linux/${ARCH}/latest/amazon-cloudwatch-agent.rpm"
PKG_TMP=/tmp/amazon-cloudwatch-agent.rpm

echo "=== Installing CloudWatch Agent ==="
if ! rpm -q amazon-cloudwatch-agent >/dev/null 2>&1; then
  sudo curl -fsSL -o "$PKG_TMP" "$PKG_URL"
  sudo rpm -Uvh "$PKG_TMP"
fi

echo "=== Installing config ==="
SRC="$(dirname "$0")/cloudwatch-agent.json"
DST=/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
sudo cp "$SRC" "$DST"
sudo chown root:root "$DST"
sudo chmod 644 "$DST"

echo "=== Starting agent ==="
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -c "file:${DST}" -s

echo "=== Status ==="
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl -m ec2 -a status

echo ""
echo "Logs will appear under CloudWatch log groups:"
echo "  /aegis/docker   (all container stdout/stderr, 14-day retention)"
echo "  /aegis/ops      (backup, prune, rollback scripts, 30-90 day retention)"
echo ""
echo "Query via CloudWatch Logs Insights:"
echo "  fields @timestamp, @message"
echo "  | filter @message like /tenant_isolation_violation|kill_switch_cross_tenant/"
echo "  | sort @timestamp desc"
echo "  | limit 50"
