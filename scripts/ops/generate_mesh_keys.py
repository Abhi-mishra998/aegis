#!/usr/bin/env python3
"""Sprint EH-5 — generate per-service ES256 mesh-JWT keypairs.

Closes architect finding: "single INTERNAL_SECRET = total mesh blast
radius." After this script runs and the docker-compose env wiring lands,
each service holds ONE private key and the public keys of every service
it accepts tokens from. Compromise of one service no longer forges
tokens for the rest of the mesh.

What the script does:
  1. For every service in SERVICES, generate a fresh P-256 ES256 keypair.
  2. Write each private key to SSM at /<env>/mesh/<service>/private (SecureString).
  3. Build the trusted-keys JSON map (service -> public PEM, base64) and
     write it once at /<env>/mesh/trusted-keys (SecureString).
  4. Print the env-block each docker-compose service needs.

After running, the operator:
  - Updates infra/docker-compose.aws.yml to inject ACP_MESH_SERVICE_NAME +
    ACP_MESH_PRIVATE_KEY_PEM + ACP_MESH_TRUSTED_KEYS for each container
    (template in this script's stdout output).
  - Rolls the ASG so each service picks up its new keys.
  - Verifies acceptance (per docs/runbooks/secrets_rotation.md §1).
  - Sets MESH_LEGACY_FALLBACK=false in identity-svc to disable the
    INTERNAL_SECRET fallback (the cutover gate). Until then mesh-JWT and
    legacy secret BOTH work, so the rollout is reversible.

Idempotency: safe to re-run — overwrites SSM. Existing services keep
authenticating with their old key until restarted.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile

# Order MATTERS only for documentation — every service holds every
# other's public key. Add a new service here when one ships.
SERVICES = [
    "gateway",
    "identity",
    "audit",
    "registry",
    "decision",
    "policy",
    "behavior",
    "usage",
    "api",
    "autonomy",
    "forensics",
    "insight",
    "identity_graph",
    "flight_recorder",
]

ENV_PREFIX = os.environ.get("AEGIS_ENV_PREFIX", "aegis-prodha")
REGION     = os.environ.get("AWS_REGION", "ap-south-1")


def _openssl(*args: str) -> bytes:
    """Run openssl, return stdout. Tiny dependency-free wrapper."""
    return subprocess.check_output(["openssl", *args])


def _gen_keypair() -> tuple[bytes, bytes]:
    """Return (private_pem, public_pem) for a fresh P-256 ES256 key."""
    with tempfile.TemporaryDirectory() as td:
        priv = f"{td}/priv.pem"
        pub  = f"{td}/pub.pem"
        _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", priv)
        _openssl("ec", "-in", priv, "-pubout", "-out", pub)
        return (
            open(priv, "rb").read(),
            open(pub, "rb").read(),
        )


def _aws_put_ssm(name: str, value: str) -> None:
    """Write SecureString param to SSM. Overwrites if exists."""
    subprocess.check_call([
        "aws", "ssm", "put-parameter",
        "--region", REGION,
        "--name",   name,
        "--value",  value,
        "--type",   "SecureString",
        "--overwrite",
    ], stdout=subprocess.DEVNULL)


def main() -> int:
    if "--dry-run" not in sys.argv and not os.environ.get("AEGIS_MESH_CONFIRM"):
        print("Refusing to write to SSM without AEGIS_MESH_CONFIRM=1.")
        print("Use --dry-run to preview the env block without touching SSM.")
        return 2
    dry = "--dry-run" in sys.argv

    print(f"# Sprint EH-5 — mesh keypair generation ({len(SERVICES)} services)")
    print(f"# env prefix: /{ENV_PREFIX}/mesh/*    region: {REGION}")
    print(f"# dry run: {dry}")
    print()

    keys: dict[str, tuple[bytes, bytes]] = {}
    for svc in SERVICES:
        priv, pub = _gen_keypair()
        keys[svc] = (priv, pub)
        if dry:
            print(f"# [dry-run] would put /{ENV_PREFIX}/mesh/{svc}/private ({len(priv)} bytes)")
        else:
            _aws_put_ssm(
                f"/{ENV_PREFIX}/mesh/{svc}/private",
                base64.b64encode(priv).decode(),
            )
            print(f"OK SSM /{ENV_PREFIX}/mesh/{svc}/private")

    # Trusted-keys map: { svc_name: base64(public_pem) }
    trusted = {svc: base64.b64encode(pub).decode() for svc, (_, pub) in keys.items()}
    trusted_json = json.dumps(trusted)
    if dry:
        print(f"# [dry-run] would put /{ENV_PREFIX}/mesh/trusted-keys ({len(trusted_json)} bytes)")
    else:
        _aws_put_ssm(f"/{ENV_PREFIX}/mesh/trusted-keys", trusted_json)
        print(f"OK SSM /{ENV_PREFIX}/mesh/trusted-keys ({len(trusted)} entries)")

    print()
    print("# ── docker-compose env block to inject per service ─────────")
    print("# (paste under each service in infra/docker-compose.aws.yml)")
    for svc in SERVICES:
        print()
        print(f"  # {svc}")
        print(f"  {svc}:")
        print(f"    environment:")
        print(f"      ACP_MESH_SERVICE_NAME: '{svc}'")
        print(f"      ACP_MESH_PRIVATE_KEY_PEM:  ${{MESH_{svc.upper()}_PRIVATE_KEY}}")
        print(f"      ACP_MESH_TRUSTED_KEYS:     ${{MESH_TRUSTED_KEYS}}")

    print()
    print("# The container's entrypoint must populate the two MESH_* env vars")
    print(f"# from SSM at /{ENV_PREFIX}/mesh/<service>/private and")
    print(f"# /{ENV_PREFIX}/mesh/trusted-keys. The ASG user_data already runs as")
    print("# instance-role and can ssm get-parameter both. Add a per-service")
    print("# block to user_data (`MESH_GATEWAY_PRIVATE_KEY=$(ssm /…/mesh/gateway/private)`)")
    print("# and pass into `docker compose up` via .env file.")

    print()
    print("# After cutover, set MESH_LEGACY_FALLBACK=false in identity to")
    print("# disable the INTERNAL_SECRET acceptance path. That makes the")
    print("# mesh truly per-service-keyed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
