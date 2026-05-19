"""Policy-as-code surface.

The ACP policy file is a YAML document checked into the customer's repo. It
defines what each agent is allowed to do. The CLI validates it before deploy;
the gateway enforces it at runtime.

Schema (v1):

    version: 1
    agent: <agent-id>
    allow:
      - tool: <tool-name>          # required
        when:                      # optional predicate
          payload.<field>: <regex>
    deny:
      - tool: <tool-name>
    autonomy:
      max_actions_per_minute: <int>
      max_blast_radius: <int>
      require_approval_for: [<tool-name>, ...]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import PolicyError

SUPPORTED_VERSIONS = {1}
VALID_TOP_LEVEL = {"version", "agent", "allow", "deny", "autonomy"}


@dataclass
class Rule:
    tool: str
    when: dict[str, str] = field(default_factory=dict)  # field → regex


@dataclass
class Autonomy:
    max_actions_per_minute: int | None = None
    max_blast_radius: int | None = None
    require_approval_for: list[str] = field(default_factory=list)


@dataclass
class Policy:
    version: int
    agent: str
    allow: list[Rule]
    deny: list[Rule]
    autonomy: Autonomy

    def matches_allow(self, tool: str, payload: dict[str, Any]) -> bool:
        return any(_rule_matches(r, tool, payload) for r in self.allow)

    def matches_deny(self, tool: str, payload: dict[str, Any]) -> bool:
        return any(_rule_matches(r, tool, payload) for r in self.deny)


def load_policy(path: str | Path) -> Policy:
    """Parse + validate a policy file. Raises PolicyError on any issue."""
    import yaml  # local import: only required when actually loading a file

    p = Path(path)
    if not p.exists():
        raise PolicyError(f"policy file not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise PolicyError(f"invalid YAML in {p}: {e}") from e
    return validate_policy(raw)


def validate_policy(raw: Any) -> Policy:
    """Validate a parsed dict and return a Policy. Raises PolicyError."""
    if not isinstance(raw, dict):
        raise PolicyError("policy root must be a mapping")

    unknown = set(raw) - VALID_TOP_LEVEL
    if unknown:
        raise PolicyError(f"unknown top-level keys: {sorted(unknown)}")

    version = raw.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise PolicyError(f"unsupported policy version: {version!r}")

    agent = raw.get("agent")
    if not isinstance(agent, str) or not agent:
        raise PolicyError("`agent` must be a non-empty string")

    allow = [_parse_rule(r, "allow", i) for i, r in enumerate(raw.get("allow") or [])]
    deny = [_parse_rule(r, "deny", i) for i, r in enumerate(raw.get("deny") or [])]

    autonomy_raw = raw.get("autonomy") or {}
    if not isinstance(autonomy_raw, dict):
        raise PolicyError("`autonomy` must be a mapping")
    autonomy = Autonomy(
        max_actions_per_minute=autonomy_raw.get("max_actions_per_minute"),
        max_blast_radius=autonomy_raw.get("max_blast_radius"),
        require_approval_for=list(autonomy_raw.get("require_approval_for") or []),
    )

    return Policy(version=version, agent=agent, allow=allow, deny=deny, autonomy=autonomy)


def _parse_rule(raw: Any, kind: str, idx: int) -> Rule:
    if not isinstance(raw, dict):
        raise PolicyError(f"{kind}[{idx}] must be a mapping")
    tool = raw.get("tool")
    if not isinstance(tool, str) or not tool:
        raise PolicyError(f"{kind}[{idx}].tool must be a non-empty string")
    when = raw.get("when") or {}
    if not isinstance(when, dict):
        raise PolicyError(f"{kind}[{idx}].when must be a mapping")
    # Compile each regex eagerly so syntax errors surface at validate time.
    for field_path, pattern in when.items():
        if not isinstance(pattern, str):
            raise PolicyError(f"{kind}[{idx}].when.{field_path} must be a string regex")
        try:
            re.compile(pattern)
        except re.error as e:
            raise PolicyError(f"{kind}[{idx}].when.{field_path}: invalid regex: {e}") from e
    return Rule(tool=tool, when=when)


def _rule_matches(rule: Rule, tool: str, payload: dict[str, Any]) -> bool:
    if rule.tool != tool and rule.tool != "*":
        return False
    for field_path, pattern in rule.when.items():
        value = _resolve(payload, field_path)
        if value is None or not re.search(pattern, str(value)):
            return False
    return True


def _resolve(obj: Any, path: str) -> Any:
    cur: Any = obj
    for part in path.split("."):
        if part == "payload":
            continue
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur
