#!/usr/bin/env bash
# Sprint EI-7 (2026-06-20). Run the chaos test suite on a staging EC2 via
# SSM RunCommand — no SSH required, no GH-Actions runner local docker
# needed (the runner sends a one-shot command and polls for completion).
#
# Why SSM, not GH-Actions-local docker:
#   - The chaos test needs `docker kill` access to the running containers
#     under load. That only works ON the EC2 host where the containers
#     are running — GH Actions runners can't reach into staging EC2 via
#     SSH (no key) and can't sideload docker into a running EC2 either.
#   - SSM RunCommand is the documented operator pathway for this kind
#     of "execute a shell command on this instance" use case. The EI-4
#     OIDC role already grants ssm:SendCommand.
#
# Usage:
#   AWS_REGION=ap-south-1 \
#   STAGING_INSTANCE_ID=$(...) \
#   bash scripts/chaos/run_via_ssm.sh
#
# Exits 0 if all chaos cases pass, non-zero on any failure.
#
# Output: pytest stdout + JSON summary on the GH-Actions step summary.

set -euo pipefail

AWS_REGION="${AWS_REGION:-ap-south-1}"
STAGING_INSTANCE_ID="${STAGING_INSTANCE_ID:-}"
CHAOS_TIMEOUT_S="${CHAOS_TIMEOUT_S:-900}"   # 15 min total budget

if [[ -z "$STAGING_INSTANCE_ID" ]]; then
    # Auto-discover by ASG tag if operator didn't pin it.
    STAGING_INSTANCE_ID="$(
        aws ec2 describe-instances --region "$AWS_REGION" \
          --filters "Name=tag:Environment,Values=staging" \
                    "Name=instance-state-name,Values=running" \
          --query 'Reservations[0].Instances[0].InstanceId' \
          --output text 2>/dev/null
    )"
    if [[ -z "$STAGING_INSTANCE_ID" || "$STAGING_INSTANCE_ID" == "None" ]]; then
        echo "FAIL — no running EC2 tagged Environment=staging in $AWS_REGION" >&2
        echo "       Did you `terraform apply -var-file=envs/staging/terraform.tfvars`?" >&2
        exit 2
    fi
fi

echo "════════════════════════════════════════"
echo " Aegis chaos drill — via SSM"
echo " region:     $AWS_REGION"
echo " instance:   $STAGING_INSTANCE_ID"
echo " timeout:    ${CHAOS_TIMEOUT_S}s"
echo "════════════════════════════════════════"

# The command we send. Single string so SSM parses it cleanly.
# The chaos suite is part of the deploy bundle at /opt/aegis/tests/chaos/.
# We run with AEGIS_BASE_URL pinned at localhost so all traffic stays on
# the box (no ALB round-trip) — the chaos is per-host, not per-tenant.
CMD='set -e
cd /opt/aegis
docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml ps --format json > /tmp/precheck-containers.json 2>&1 || true
PYTHONPATH=. AEGIS_BASE_URL=http://localhost:8000 \
  python3 -m pytest -m chaos -v --tb=short --color=no \
  tests/chaos/test_resilience_live.py 2>&1
'

echo "→ Sending pytest command to instance via SSM..."
CMD_ID="$(aws ssm send-command \
    --region "$AWS_REGION" \
    --instance-ids "$STAGING_INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --comment "Aegis nightly chaos drill" \
    --timeout-seconds "$CHAOS_TIMEOUT_S" \
    --parameters "commands=[\"$CMD\"]" \
    --query 'Command.CommandId' --output text)"
echo "  CMD_ID=$CMD_ID"

echo "→ Polling for completion (every 10s, max ${CHAOS_TIMEOUT_S}s)..."
SECONDS_WAITED=0
until status="$(aws ssm get-command-invocation \
        --region "$AWS_REGION" \
        --command-id "$CMD_ID" \
        --instance-id "$STAGING_INSTANCE_ID" \
        --query 'Status' --output text 2>/dev/null)"; do
    sleep 5
done

while [[ "$status" == "Pending" || "$status" == "InProgress" || "$status" == "Delayed" ]]; do
    sleep 10
    SECONDS_WAITED=$((SECONDS_WAITED + 10))
    status="$(aws ssm get-command-invocation \
        --region "$AWS_REGION" \
        --command-id "$CMD_ID" \
        --instance-id "$STAGING_INSTANCE_ID" \
        --query 'Status' --output text)"
    echo "  [${SECONDS_WAITED}s] status=$status"
    if [[ $SECONDS_WAITED -ge $CHAOS_TIMEOUT_S ]]; then
        echo "FAIL — chaos drill exceeded ${CHAOS_TIMEOUT_S}s timeout" >&2
        exit 3
    fi
done

echo ""
echo "═════════ Final status: $status ═════════"
echo ""

# Capture stdout regardless of pass/fail — operator needs to see why if it failed.
STDOUT="$(aws ssm get-command-invocation \
    --region "$AWS_REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$STAGING_INSTANCE_ID" \
    --query 'StandardOutputContent' --output text)"
STDERR="$(aws ssm get-command-invocation \
    --region "$AWS_REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$STAGING_INSTANCE_ID" \
    --query 'StandardErrorContent' --output text)"

echo "=== STDOUT ==="
echo "$STDOUT" | tail -100
if [[ -n "$STDERR" && "$STDERR" != "None" ]]; then
    echo ""
    echo "=== STDERR ==="
    echo "$STDERR" | tail -40
fi

if [[ "$status" != "Success" ]]; then
    echo "" >&2
    echo "FAIL — chaos drill ended in status=$status" >&2
    exit 1
fi

echo ""
echo "✓ Chaos drill PASS — all parametrized cases survived their SLO budgets."
