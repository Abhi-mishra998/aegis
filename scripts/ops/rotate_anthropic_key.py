#!/usr/bin/env python3
"""Sprint 17 — Anthropic upstream API-key rotation helper.

USAGE:
    # Dry-run — prints what would happen, no AWS calls.
    python3 scripts/ops/rotate_anthropic_key.py --dry-run

    # Push a new sk-ant- key to SSM and restart the gateway on
    # every prod-ha instance:
    python3 scripts/ops/rotate_anthropic_key.py \\
        --new-key 'sk-ant-api03-NEW_VALUE_FROM_CONSOLE' \\
        --ssm-parameter '/aegis-prodha/anthropic/upstream-key' \\
        --instance-ids i-076882fbef70af91f i-05a13cd061d30849d \\
        --restart \\
        --smoke

PRE-REQ (operator only — script does not do this):
    1. Sign in at https://console.anthropic.com/settings/keys
    2. Click "Revoke" on the existing key (sk-ant-…wA-LTfXVQAA)
    3. Click "Create Key", copy the new value
    4. Paste it into --new-key

The script:
  1. Validates the key looks like a real ``sk-ant-`` value.
  2. ``aws ssm put-parameter --type SecureString --overwrite``.
  3. With --restart: SSM run-command writes the new key to
     ``/opt/aegis/infra/.env.upstream_anthropic`` on each instance
     and ``docker restart acp_gateway``.
  4. With --smoke: hits ``/v1/messages`` with a known good employee
     virtual key and asserts a 200.

By design, the script does NOT:
  - Touch the Anthropic console (no Backend API for key issuance).
  - Restart anything without ``--restart``.
  - Mutate the repo's .env files.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

_KEY_PATTERN = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")


def _run(cmd: list[str], *, dry_run: bool, check: bool = True) -> str:
    print(f"  $ {' '.join(cmd)}", file=sys.stderr)
    if dry_run:
        return ""
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        print(f"  FAIL: {result.stderr}", file=sys.stderr)
        sys.exit(result.returncode)
    return result.stdout


def _wait_for_ssm(command_id: str, instance_id: str, region: str, *, timeout: int = 90) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = _run([
            "aws", "ssm", "get-command-invocation",
            "--command-id", command_id,
            "--instance-id", instance_id,
            "--region", region,
            "--query", "Status",
            "--output", "text",
        ], dry_run=False, check=False).strip()
        if out in ("Success", "Failed", "Cancelled", "TimedOut"):
            print(f"  {instance_id} → {out}", file=sys.stderr)
            return
        time.sleep(3)
    print(f"  {instance_id} → timed out waiting", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--new-key", help="New sk-ant- key from console.anthropic.com")
    p.add_argument(
        "--ssm-parameter",
        default="/aegis-prodha/anthropic/upstream-key",
        help="SSM parameter name to overwrite",
    )
    p.add_argument(
        "--instance-ids",
        nargs="+",
        default=["i-076882fbef70af91f", "i-05a13cd061d30849d"],
        help="EC2 instances running the gateway",
    )
    p.add_argument("--region", default="ap-south-1")
    p.add_argument("--restart", action="store_true", help="Restart gateway after rotation")
    p.add_argument("--smoke", action="store_true", help="Hit /v1/messages after restart")
    p.add_argument(
        "--smoke-key",
        help="An acp_emp_… key to use for the smoke test (required with --smoke)",
    )
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not args.dry_run and not args.new_key:
        p.error("--new-key is required (or use --dry-run)")
    if args.new_key and not _KEY_PATTERN.match(args.new_key):
        p.error(
            "New key does not look like an Anthropic API key — expected "
            "sk-ant-… of at least 20 characters",
        )
    if args.smoke and not args.smoke_key:
        p.error("--smoke requires --smoke-key (an acp_emp_ key for the proxy call)")

    print(f"→ writing new key to SSM parameter {args.ssm_parameter}", file=sys.stderr)
    if not args.dry_run:
        _run([
            "aws", "ssm", "put-parameter",
            "--name", args.ssm_parameter,
            "--value", args.new_key,
            "--type", "SecureString",
            "--overwrite",
            "--region", args.region,
        ], dry_run=False)
    else:
        print("  (dry-run) would put-parameter with new value", file=sys.stderr)

    if not args.restart:
        print("→ skipping restart (no --restart). Re-run with --restart to apply.", file=sys.stderr)
        return 0

    print(f"→ pushing new env + restarting gateway on {len(args.instance_ids)} hosts", file=sys.stderr)
    if args.dry_run:
        for iid in args.instance_ids:
            print(f"  (dry-run) would SSM run-command on {iid}", file=sys.stderr)
        return 0

    # Inline shell — SSM Run-Command can't easily template a multi-line
    # script with quoted env values, so write a one-liner that pulls
    # the new key fresh from SSM and updates the gateway's env. Avoids
    # leaking the raw key into Run-Command logs.
    cmd = [
        "aws", "ssm", "send-command",
        "--instance-ids", *args.instance_ids,
        "--document-name", "AWS-RunShellScript",
        "--parameters", (
            "commands=["
            "\"set -e\","
            f"\"NEW=$(aws ssm get-parameter --name {args.ssm_parameter} "
            f"--with-decryption --region {args.region} --query Parameter.Value --output text)\","
            "\"docker exec acp_gateway sh -c \\\"export UPSTREAM_ANTHROPIC_KEY=$NEW\\\" "
            "|| true\","
            "\"sed -i.bak '/^UPSTREAM_ANTHROPIC_KEY=/d' /opt/aegis/infra/.env || true\","
            "\"echo \\\"UPSTREAM_ANTHROPIC_KEY=$NEW\\\" >> /opt/aegis/infra/.env\","
            "\"docker restart acp_gateway\","
            "\"sleep 5\","
            "\"docker ps --format '{{.Names}}: {{.Status}}' | grep gateway\""
            "]"
        ),
        "--region", args.region,
        "--query", "Command.CommandId",
        "--output", "text",
    ]
    command_id = _run(cmd, dry_run=False).strip()
    print(f"  SSM command-id: {command_id}", file=sys.stderr)
    for iid in args.instance_ids:
        _wait_for_ssm(command_id, iid, args.region)

    if not args.smoke:
        print("→ done (no --smoke). Manually verify /v1/messages.", file=sys.stderr)
        return 0

    print("→ smoke-testing /v1/messages with a known good employee key", file=sys.stderr)
    body = (
        '{"model":"claude-haiku-4-5","max_tokens":20,'
        '"messages":[{"role":"user","content":"rotation-smoke"}]}'
    )
    result = subprocess.run(
        [
            "curl", "-sS", "-w", "\nHTTP=%{http_code}\n",
            "-X", "POST", "https://ha.aegisagent.in/v1/messages",
            "-H", f"x-api-key: {args.smoke_key}",
            "-H", "anthropic-version: 2023-06-01",
            "-H", "Content-Type: application/json",
            "-d", body,
        ],
        capture_output=True, text=True, check=False,
    )
    print(result.stdout)
    if "HTTP=200" not in result.stdout:
        print("✗ smoke test FAILED — rotation not yet effective", file=sys.stderr)
        return 1
    print("✓ rotation effective end-to-end", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
