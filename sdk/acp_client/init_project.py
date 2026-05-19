"""Project scaffolder — `acp init`.

Creates the canonical .acp/ layout in the customer's repo so they go from
`pip install acp` to "I have a working policy + protected agent example" in
one command.

Files created (relative to the target directory):

    .acp/policy.yaml      — starter policy with sensible defaults
    .acp/example.py       — 5-line integration sketch with comments

No file is overwritten unless --force is passed. Existing .acp/ files are
left alone individually; the command does NOT modify the customer's
.gitignore or anything outside .acp/.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


POLICY_TEMPLATE = """\
# ACP policy file — committed to your repo, validated in CI.
#
# Validate:    acp validate .acp/policy.yaml
# Reference:   https://acp.example.com/docs/policy
#
# Two-section model:
#   allow: agent may invoke these tools (with optional `when` predicates)
#   deny:  always rejected, evaluated AFTER allow — deny always wins
#   autonomy: global guardrails on the agent

version: 1
agent: {agent_id}

allow:
  # Read-only DB queries OK
  - tool: db.query
    when:
      payload.args.0: "^SELECT"

  # Calls to your own internal services OK
  - tool: http.get
    when:
      payload.args.0: "^https://api\\\\.internal\\\\."

  # Public search OK
  - tool: search

deny:
  # Destructive SQL — always
  - tool: db.query
    when:
      payload.args.0: "DROP|TRUNCATE|DELETE|ALTER"

  # Shell execution — never
  - tool: shell.exec

autonomy:
  max_actions_per_minute: 60
  max_blast_radius: 10
  require_approval_for:
    - send_email
    - transfer_funds
    - delete_user
"""


EXAMPLE_TEMPLATE = '''\
"""Minimal ACP integration example.

Five lines to wrap an agent function so every call is policy-checked and
audit-signed by the gateway:

    1. Create the client (reads ACP_API_KEY + ACP_BASE_URL from env).
    2. Decorate the function with @client.protect(agent_id=...).
    3. Call the function normally — denials raise acp.DeniedError.

Replace the body of `query` with your real agent action.
"""
from __future__ import annotations

import os

import acp

client = acp.Client(
    api_key=os.environ["ACP_API_KEY"],
    base_url=os.environ.get("ACP_BASE_URL", "https://acp.example.com"),
)


@client.protect(agent_id="{agent_id}")
def query(sql: str) -> list[dict]:
    # Replace this with your real DB call.
    return [{{"row": 1, "sql": sql}}]


if __name__ == "__main__":
    try:
        rows = query("SELECT * FROM users LIMIT 1")
        print("ok:", rows)
    except acp.DeniedError as e:
        print(f"blocked by ACP: {{e.reason}} (decision {{e.decision_id}})")
'''


@dataclass
class InitResult:
    created: list[Path]
    skipped: list[Path]   # already existed and --force was not set


def init_project(*, target_dir: str | Path, agent_id: str = "agent_default", force: bool = False) -> InitResult:
    """Scaffold .acp/ inside `target_dir`. Returns what was created vs skipped.

    Raises FileNotFoundError if `target_dir` doesn't exist.
    Raises NotADirectoryError if it isn't a directory.
    """
    target = Path(target_dir)
    if not target.exists():
        raise FileNotFoundError(f"target directory does not exist: {target}")
    if not target.is_dir():
        raise NotADirectoryError(f"target is not a directory: {target}")
    if not agent_id:
        raise ValueError("agent_id must be non-empty")

    acp_dir = target / ".acp"
    acp_dir.mkdir(exist_ok=True)

    created: list[Path] = []
    skipped: list[Path] = []

    files = [
        (acp_dir / "policy.yaml",  POLICY_TEMPLATE.format(agent_id=agent_id)),
        (acp_dir / "example.py",   EXAMPLE_TEMPLATE.format(agent_id=agent_id)),
    ]

    for path, body in files:
        if path.exists() and not force:
            skipped.append(path)
            continue
        path.write_text(body)
        created.append(path)

    return InitResult(created=created, skipped=skipped)
