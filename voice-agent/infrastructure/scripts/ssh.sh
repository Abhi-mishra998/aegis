#!/bin/bash
# Open an SSH session into the instance.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
PEM="$(tf_pem_path)"
PUBLIC_IP="$(tf_public_ip)"
exec ssh -i "$PEM" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP" "$@"
