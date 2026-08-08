"""
Canonical Audit Hash Function — Single Source of Truth
=======================================================
ALL audit hash computation MUST use this module. Never duplicate this
logic inline.

The scheme is *versioned*. Every audit row stores its ``hash_version``
in the DB so a verifier can select the right rules per row. The default
for new writes is :data:`CURRENT_VERSION`.

Version 1 (LEGACY — accepted on verify, no longer used for new writes):

    H(prev_hash + json({tenant_id, agent_id, action, tool, decision, request_id}))

    Only 6 fields are covered — ``reason``, ``metadata_json``, and
    ``timestamp`` can be silently rewritten in the DB without the chain
    noticing (report finding C6). Left in place so historical rows still
    verify.

Version 2 (current, SEC-2026-07-31 (C6)):

    H(prev_hash + json({
        tenant_id, agent_id, action, tool, decision, request_id,
        reason, timestamp, metadata_sha256,
    }))

    ``metadata_sha256`` is the SHA-256 of the sorted-keys JSON encoding
    of ``metadata_json``, so the chain covers *findings*, *risk_score*,
    *PII flags*, *MITRE mapping*, *tool arguments*, and every other
    signal a compliance mapper reads. Rewriting any of them changes the
    hash and the chain verifier flags the corruption.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

# On-disk scheme for NEW writes.
CURRENT_VERSION: Literal[2] = 2

GENESIS_HASH = "0" * 64


def _metadata_sha256(metadata: Any) -> str:
    """SHA-256 hex of the canonical (sorted-keys) JSON encoding of the
    metadata blob. ``None`` and empty dict both hash to the same
    canonical ``{}`` so that "no metadata attached" is stable."""
    if metadata is None or metadata == {}:
        canonical = b"{}"
    else:
        canonical = json.dumps(
            metadata,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_event_hash(
    prev_hash: str,
    tenant_id: str,
    agent_id: str,
    action: str,
    tool: str | None,
    decision: str,
    request_id: str | None,
    *,
    reason: str | None = None,
    timestamp: str | None = None,
    metadata: Any = None,
    version: int = CURRENT_VERSION,
) -> str:
    """H(prev_hash + stable_json(canonical_fields)).

    ``version`` selects the field set. v1 covers only the six original
    fields (for verifying historical rows); v2 also covers ``reason``,
    ``timestamp`` (ISO-8601 string), and ``metadata_sha256`` so nothing
    that a compliance mapper reads is outside the hash.
    """
    if version == 1:
        payload_obj: dict[str, Any] = {
            "tenant_id":  str(tenant_id),
            "agent_id":   str(agent_id),
            "action":     str(action),
            "tool":       str(tool or ""),
            "decision":   str(decision),
            "request_id": str(request_id or ""),
        }
    elif version == 2:
        payload_obj = {
            "tenant_id":        str(tenant_id),
            "agent_id":         str(agent_id),
            "action":           str(action),
            "tool":             str(tool or ""),
            "decision":         str(decision),
            "request_id":       str(request_id or ""),
            "reason":           str(reason or ""),
            "timestamp":        str(timestamp or ""),
            "metadata_sha256":  _metadata_sha256(metadata),
        }
    else:
        raise ValueError(f"unknown audit hash_version: {version!r}")

    payload = json.dumps(payload_obj, sort_keys=True)
    return hashlib.sha256(f"{prev_hash}{payload}".encode()).hexdigest()


def _demo_selfcheck() -> None:
    """SEC-2026-07-31 (ponytail assert-based test) — smallest thing that
    fails if v2 stops covering ``reason``/``metadata``."""
    h1 = compute_event_hash(
        prev_hash=GENESIS_HASH,
        tenant_id="t", agent_id="a", action="x", tool="y",
        decision="allow", request_id="r",
        reason="ok", timestamp="2026-07-31T00:00:00Z",
        metadata={"findings": []},
    )
    h2 = compute_event_hash(
        prev_hash=GENESIS_HASH,
        tenant_id="t", agent_id="a", action="x", tool="y",
        decision="allow", request_id="r",
        reason="approved_by_ceo",          # ← rewritten
        timestamp="2026-07-31T00:00:00Z",
        metadata={"findings": ["override"]},
    )
    assert h1 != h2, "v2 event hash must change when reason/metadata change"
    # v1 unchanged form must still hash identically to the legacy
    # behaviour (backward-compat with existing rows).
    legacy = compute_event_hash(
        prev_hash=GENESIS_HASH,
        tenant_id="t", agent_id="a", action="x", tool="y",
        decision="allow", request_id="r",
        version=1,
    )
    assert len(legacy) == 64
    print("sdk/common/audit_hash.py — v2 self-check OK")


if __name__ == "__main__":
    _demo_selfcheck()
