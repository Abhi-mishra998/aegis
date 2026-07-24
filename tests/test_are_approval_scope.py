"""Q37 regression: an ARE approval authorizes ONE specific rule
(§5.7 single-action-binding). The re-queued incident must carry the
approved rule id so the worker skips every other rule that happens
to match the same incident. Prior code re-queued only
`_manual_approved=True` and the worker fired every matching rule.
"""
from __future__ import annotations

import inspect


def test_approve_pending_stamps_approved_rule_id():
    """Whitebox: the approval endpoint must include _approved_rule_id
    from the pending payload's `rule_id` field on re-queue."""
    from services.api.router.auto_response import approve_pending
    src = inspect.getsource(approve_pending)
    assert '_approved_rule_id' in src, (
        "approve_pending no longer stamps _approved_rule_id on re-queue "
        "— every rule fires under _manual_approved again"
    )
    # The pending payload's rule_id is the source.
    assert 'pending.get("rule_id")' in src


def test_process_incident_gates_on_approved_rule_id():
    """Whitebox: process_incident must skip candidates whose id
    doesn't match _approved_rule_id when the incident is marked
    approved."""
    from services.api.are_worker import process_incident
    src = inspect.getsource(process_incident)
    assert '_approved_rule_id' in src
    assert 'approval_scope_skip' in src, (
        "the skip must be AUDITED as approval_scope_skip so the trail "
        "shows which rules were refused authorization"
    )
    # The audit metadata references the approved rule id for traceability.
    assert 'approved_rule_id_mismatch' in src or 'approved_rule_id' in src


def test_pending_payload_contract_carries_rule_id():
    """Sanity: the source of the pending payload (_persist_pending in
    are_worker) still stores rule_id — if this ever drops, approvals
    lose the id we now depend on for scope enforcement."""
    from services.api import are_worker as _mod
    src = inspect.getsource(_mod)
    # Look for the persist point.
    assert '"rule_id":      rule_id,' in src or '"rule_id": rule_id' in src
