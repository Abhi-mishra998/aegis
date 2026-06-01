#!/bin/bash
# Runs terraform plan with AWS creds + runtime secrets injected from .env files.
# Non-billable — does not create resources. Output is reviewed before apply.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
load_tf_vars_from_agent_env

cd "$INFRA_DIR"
terraform init -upgrade
terraform plan -out=tfplan
echo
echo "Plan written to $INFRA_DIR/tfplan."
echo "To apply: ./scripts/apply.sh"
