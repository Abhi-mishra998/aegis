#!/usr/bin/env python3
"""
Sprint 10 — Clerk webhook-secret rotation helper.

USAGE:
    # Dry-run — prints what would happen, no AWS calls.
    python3 scripts/ops/rotate_clerk_keys.py --dry-run

    # Push a new whsec_ value to SSM (you fetch the new whsec_ from
    # the Clerk dashboard yourself; we don't talk to the Clerk API for
    # the rotation step):
    python3 scripts/ops/rotate_clerk_keys.py \\
        --new-secret 'whsec_NEW_VALUE_FROM_CLERK_DASHBOARD' \\
        --ssm-parameter '/aegis-prodha/clerk/webhook-secret' \\
        --asg acp-prodha-asg-20260613103432397400000003

The script:
  1. Validates the secret looks like a real ``whsec_`` value.
  2. ``aws ssm put-parameter --type SecureString --overwrite``.
  3. Optionally runs an SSM ``send-command`` to update
     ``/opt/aegis/infra/.env`` on every instance in the ASG and
     ``docker compose up -d --force-recreate identity gateway``.
  4. Optionally triggers an ASG instance refresh so the next bundle
     deploy picks up the new env via fresh launch.

By design, the script does NOT:
  - Fetch a new whsec_ from Clerk's dashboard (no Backend API for it).
  - Restart any service without the operator's explicit ``--restart`` flag.
  - Modify the repo's .env files (those are dev-side; prod env lives
    on the instances).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

_WHSEC_PATTERN = re.compile(r"^whsec_[A-Za-z0-9+/=_-]{20,}$")


def _run(cmd: list[str], *, dry_run: bool, check: bool = True) -> str:
    """Run a shell command unless dry-run; return stdout."""
    print(f"  $ {' '.join(cmd)}", file=sys.stderr)
    if dry_run:
        return ""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"  FAIL: {result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotate Clerk whsec_ webhook signing secret on prod.",
    )
    parser.add_argument(
        "--new-secret", required=True,
        help="The new whsec_ value (fetch from Clerk dashboard → Webhooks).",
    )
    parser.add_argument(
        "--ssm-parameter", default="/aegis-prodha/clerk/webhook-secret",
        help="SSM parameter path that holds the secret.",
    )
    parser.add_argument(
        "--region", default="ap-south-1", help="AWS region.",
    )
    parser.add_argument(
        "--asg", default="",
        help="ASG name. If set + --restart, runs SSM send-command on every "
             "instance to overlay /opt/aegis/infra/.env and force-recreate "
             "identity + gateway containers.",
    )
    parser.add_argument(
        "--env-var", default="CLERK_WEBHOOK_SECRET",
        help="Env-var name to set on /opt/aegis/infra/.env.",
    )
    parser.add_argument(
        "--restart", action="store_true",
        help="If set, push the new secret onto live instances + restart.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Trigger an ASG instance refresh after rotation (slower but safer).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print every command without executing.",
    )
    args = parser.parse_args()

    if not _WHSEC_PATTERN.match(args.new_secret):
        print(
            "FAIL: --new-secret does not look like a whsec_ value "
            "(expected ^whsec_[A-Za-z0-9+/=_-]{20,}$).",
            file=sys.stderr,
        )
        sys.exit(2)

    # 1. Push the new secret to SSM (SecureString).
    print(
        f"→ Pushing new secret to SSM parameter {args.ssm_parameter} "
        f"in {args.region}.",
        file=sys.stderr,
    )
    _run(
        [
            "aws", "--region", args.region, "ssm", "put-parameter",
            "--name", args.ssm_parameter,
            "--type", "SecureString",
            "--value", args.new_secret,
            "--overwrite",
        ],
        dry_run=args.dry_run,
    )

    if args.restart and args.asg:
        instances = _run(
            [
                "aws", "--region", args.region,
                "autoscaling", "describe-auto-scaling-groups",
                "--auto-scaling-group-names", args.asg,
                "--query", "AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId",
                "--output", "json",
            ],
            dry_run=args.dry_run,
        )
        try:
            instance_ids = json.loads(instances) if instances else []
        except json.JSONDecodeError:
            instance_ids = []

        if not instance_ids and not args.dry_run:
            print(
                "FAIL: no InService instances under that ASG; nothing to update.",
                file=sys.stderr,
            )
            sys.exit(3)

        print(
            f"→ Found {len(instance_ids) or 'N/A'} live instances; pushing "
            "new env via SSM send-command.",
            file=sys.stderr,
        )

        # IMPORTANT: the new secret value rides through SSM Send Command in
        # plaintext (visible to anyone with ssm:GetCommandInvocation on this
        # account). For tighter ops, prefer --refresh which makes new
        # instances fetch the secret from SSM SecureString at boot.
        update_script = (
            "set -euo pipefail\n"
            f"ENV_FILE=/opt/aegis/infra/.env\n"
            f"BACKUP=/opt/aegis/infra/.env.bak-$(date -u +%Y%m%dT%H%M%SZ)\n"
            "cp \"$ENV_FILE\" \"$BACKUP\"\n"
            f"grep -v -E \"^{args.env_var}=\" \"$BACKUP\" > \"$ENV_FILE\"\n"
            f"echo '{args.env_var}={args.new_secret}' >> \"$ENV_FILE\"\n"
            "cd /opt/aegis\n"
            "docker compose -f infra/docker-compose.yml -f infra/docker-compose.aws.yml up -d --force-recreate --no-deps identity gateway\n"
        )

        params = json.dumps({"commands": [update_script]})
        _run(
            [
                "aws", "--region", args.region, "ssm", "send-command",
                "--instance-ids", *instance_ids,
                "--document-name", "AWS-RunShellScript",
                "--comment", f"Rotate {args.env_var}",
                "--parameters", params,
            ],
            dry_run=args.dry_run,
        )

    if args.refresh and args.asg:
        print(f"→ Triggering ASG instance refresh on {args.asg}.", file=sys.stderr)
        _run(
            [
                "aws", "--region", args.region, "autoscaling",
                "start-instance-refresh",
                "--auto-scaling-group-name", args.asg,
                "--preferences", '{"MinHealthyPercentage":50,"InstanceWarmup":300}',
            ],
            dry_run=args.dry_run,
        )

    print("✓ Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
