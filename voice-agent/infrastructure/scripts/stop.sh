#!/bin/bash
# Stop the EC2 instance. Stops the GPU clock; EBS keeps billing (~$3/mo).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
INSTANCE_ID="$(tf_instance_id)"
echo "Stopping $INSTANCE_ID..."
aws ec2 stop-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-stopped --instance-ids "$INSTANCE_ID"
echo "Stopped. (EBS volume still bills ~\$3/mo while data is preserved.)"
