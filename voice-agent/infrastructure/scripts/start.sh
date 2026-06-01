#!/bin/bash
# Start the EC2 instance + wait until SSH is up.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
INSTANCE_ID="$(tf_instance_id)"
PUBLIC_IP="$(tf_public_ip)"

echo "Starting $INSTANCE_ID..."
aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-status-ok --instance-ids "$INSTANCE_ID"

echo "Running at $PUBLIC_IP."
echo "  ssh: ./scripts/ssh.sh"
echo "  stop when done: ./scripts/stop.sh"
