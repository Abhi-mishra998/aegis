#!/bin/bash
# Applies the saved plan from plan.sh. BILLABLE — only run after explicit cost approval.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/_lib.sh"

load_aws_env
load_tf_vars_from_agent_env

cd "$INFRA_DIR"
if [ ! -f tfplan ]; then
  echo "No tfplan found. Run ./scripts/plan.sh first."
  exit 1
fi

terraform apply tfplan

echo
echo "==> Outputs"
terraform output

echo
echo "Next: ./scripts/deploy.sh (uploads code, installs venv, runs ingest, starts agent)"
