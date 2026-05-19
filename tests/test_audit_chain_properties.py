"""
Property tests for the audit hash chain.

Tests focus on the pure-function layer (`compute_event_hash`, `compute_chain_shard`,
`GENESIS_HASH`) — no database required.

Run with:
    .venv/bin/python -m pytest tests/test_audit_chain_properties.py -v
"""
from __future__ import annotations

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from sdk.common.audit_hash import GENESIS_HASH, compute_event_hash
from services.audit.writer import AUDIT_CHAIN_SHARD_COUNT, compute_chain_shard

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_TENANT = st.uuids().map(str)
_AGENT  = st.uuids().map(str)
_ACTION = st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd", "Pd")))
_TOOL   = st.one_of(st.none(), st.text(min_size=0, max_size=50))
_DEC    = st.text(min_size=1, max_size=20)
_REQ    = st.one_of(st.none(), st.text(min_size=0, max_size=64))
_HASH   = st.text(min_size=64, max_size=64,
                  alphabet=st.characters(whitelist_categories=("Nd",), whitelist_characters="abcdef"))


# ---------------------------------------------------------------------------
# 1. Output is always a 64-char hex string
# ---------------------------------------------------------------------------

@given(
    prev_hash=_HASH,
    tenant_id=_TENANT,
    agent_id=_AGENT,
    action=_ACTION,
    tool=_TOOL,
    decision=_DEC,
    request_id=_REQ,
)
@hyp_settings(max_examples=200)
def test_hash_output_is_64_char_hex(
    prev_hash, tenant_id, agent_id, action, tool, decision, request_id,
):
    h = compute_event_hash(
        prev_hash=prev_hash,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action=action,
        tool=tool,
        decision=decision,
        request_id=request_id,
    )
    assert len(h) == 64, f"expected 64-char hex, got len={len(h)}"
    assert all(c in "0123456789abcdef" for c in h), f"non-hex chars in hash: {h!r}"


# ---------------------------------------------------------------------------
# 2. Determinism: same inputs → same hash (always)
# ---------------------------------------------------------------------------

@given(
    prev_hash=_HASH,
    tenant_id=_TENANT,
    agent_id=_AGENT,
    action=_ACTION,
    tool=_TOOL,
    decision=_DEC,
    request_id=_REQ,
)
@hyp_settings(max_examples=200)
def test_hash_is_deterministic(
    prev_hash, tenant_id, agent_id, action, tool, decision, request_id,
):
    kwargs = {"prev_hash": prev_hash, "tenant_id": tenant_id, "agent_id": agent_id,
                  "action": action, "tool": tool, "decision": decision, "request_id": request_id}
    assert compute_event_hash(**kwargs) == compute_event_hash(**kwargs)


# ---------------------------------------------------------------------------
# 3. Tamper detection: mutate any canonical field → hash changes
# ---------------------------------------------------------------------------

def _base_hash(
    prev_hash="a" * 64,
    tenant_id="00000000-0000-0000-0000-000000000001",
    agent_id="00000000-0000-0000-0000-000000000002",
    action="execute_tool",
    tool="read_file",
    decision="allow",
    request_id="req-1234",
) -> str:
    return compute_event_hash(
        prev_hash=prev_hash,
        tenant_id=tenant_id,
        agent_id=agent_id,
        action=action,
        tool=tool,
        decision=decision,
        request_id=request_id,
    )


def test_tamper_prev_hash_detected():
    original = _base_hash()
    tampered = _base_hash(prev_hash="b" * 64)
    assert original != tampered


def test_tamper_tenant_id_detected():
    original = _base_hash()
    tampered = _base_hash(tenant_id="00000000-0000-0000-0000-000000000099")
    assert original != tampered


def test_tamper_agent_id_detected():
    original = _base_hash()
    tampered = _base_hash(agent_id="00000000-0000-0000-0000-000000000099")
    assert original != tampered


def test_tamper_action_detected():
    original = _base_hash()
    tampered = _base_hash(action="deny")
    assert original != tampered


def test_tamper_tool_detected():
    original = _base_hash()
    tampered = _base_hash(tool="write_file")
    assert original != tampered


def test_tamper_decision_detected():
    original = _base_hash()
    tampered = _base_hash(decision="deny")
    assert original != tampered


def test_tamper_request_id_detected():
    original = _base_hash()
    tampered = _base_hash(request_id="req-9999")
    assert original != tampered


# ---------------------------------------------------------------------------
# 4. Chain integrity: each entry's prev_hash == previous entry's event_hash
# ---------------------------------------------------------------------------

def test_chain_links_correctly():
    """Simulate a 5-entry chain on a single shard and verify linkage."""
    events = [
        {"action": "execute_tool", "tool": "read_file",  "decision": "allow",  "request_id": f"req-{i}"}
        for i in range(5)
    ]
    tenant_id = "00000000-0000-0000-0000-000000000001"
    agent_id  = "00000000-0000-0000-0000-000000000002"

    prev = GENESIS_HASH
    hashes: list[str] = []
    for evt in events:
        h = compute_event_hash(
            prev_hash=prev,
            tenant_id=tenant_id,
            agent_id=agent_id,
            **evt,
        )
        hashes.append(h)
        prev = h

    # Verify each link
    chain_prev = GENESIS_HASH
    for i, evt in enumerate(events):
        recomputed = compute_event_hash(
            prev_hash=chain_prev,
            tenant_id=tenant_id,
            agent_id=agent_id,
            **evt,
        )
        assert recomputed == hashes[i], f"chain broken at position {i}"
        chain_prev = recomputed


# ---------------------------------------------------------------------------
# 5. Genesis hash is the correct sentinel (64 zero-chars)
# ---------------------------------------------------------------------------

def test_genesis_hash_is_64_zeros():
    assert GENESIS_HASH == "0" * 64
    assert len(GENESIS_HASH) == 64


# ---------------------------------------------------------------------------
# 6. Shard stability: same request_id → same shard, always
# ---------------------------------------------------------------------------

@given(request_id=st.text(min_size=1, max_size=128))
@hyp_settings(max_examples=300)
def test_shard_is_stable(request_id):
    s1 = compute_chain_shard(request_id)
    s2 = compute_chain_shard(request_id)
    assert s1 == s2, f"shard not deterministic for {request_id!r}"


# ---------------------------------------------------------------------------
# 7. Shard is always in [0, AUDIT_CHAIN_SHARD_COUNT)
# ---------------------------------------------------------------------------

@given(request_id=st.one_of(st.none(), st.text(min_size=0, max_size=128)))
@hyp_settings(max_examples=300)
def test_shard_in_valid_range(request_id):
    s = compute_chain_shard(request_id)
    assert 0 <= s < AUDIT_CHAIN_SHARD_COUNT, (
        f"shard {s} outside [0, {AUDIT_CHAIN_SHARD_COUNT})"
    )


# ---------------------------------------------------------------------------
# 8. None / empty request_id falls back to shard 0
# ---------------------------------------------------------------------------

def test_shard_none_is_zero():
    assert compute_chain_shard(None) == 0


def test_shard_empty_string_is_zero():
    assert compute_chain_shard("") == 0


# ---------------------------------------------------------------------------
# 9. Two different request_ids can hash to different shards
#    (distribution is not degenerate — shard 0 is not the only possible output)
# ---------------------------------------------------------------------------

def test_shard_distribution_not_degenerate():
    """At least 4 distinct shard values across 1000 random request IDs."""
    shards = {compute_chain_shard(f"req-{i}") for i in range(1000)}
    assert len(shards) >= 4, f"shard function looks degenerate: only {len(shards)} distinct values"
