#!/bin/bash
# Print instance state + recent agent log.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
INSTANCE_ID="$(tf_instance_id)"

STATE=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text)
echo "Instance state: $STATE"

if [ "$STATE" = "running" ]; then
  PEM="$(tf_pem_path)"
  PUBLIC_IP="$(tf_public_ip)"
  echo "Public IP: $PUBLIC_IP"
  echo "Recent agent log (last 40 lines):"
  ssh -i "$PEM" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP" \
    "sudo journalctl -u aegis-agent.service --no-pager -n 40" || true
fi
