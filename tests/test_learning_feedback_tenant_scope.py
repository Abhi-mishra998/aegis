"""Q35 regression: learning.apply_feedback's feedback_key must include
tenant_id so feedback from tenant A doesn't blend into tenant B's
adaptive-weight counters for the same agent_id."""
from __future__ import annotations

import inspect


def test_feedback_key_uses_get_key_helper():
    """Whitebox: the feedback key MUST go through the tenant-scoping
    helper `_get_key(tenant, agent, "feedback")`, not a bare
    `f"acp:feedback:{agent_id}"` that omits tenant_id."""
    from services.learning.service import LearningService
    src = inspect.getsource(LearningService.apply_feedback)
    # Old bare form is gone.
    assert 'f"acp:feedback:{str(agent_id)}"' not in src
    # New form uses _get_key (tenant-scoped).
    assert '_get_key(tenant_id, agent_id, "feedback")' in src


def test_get_key_shape_includes_tenant_and_agent():
    """Sanity: _get_key produces distinct keys for the same agent_id
    across different tenants. If someone reverts to omit tenant_id,
    this canary catches the collision that Q35 fixed."""
    import uuid

    from services.learning.service import LearningService
    svc = LearningService.__new__(LearningService)  # skip __init__ (no Redis)
    tid_a = uuid.uuid4()
    tid_b = uuid.uuid4()
    agent = uuid.uuid4()
    ka = svc._get_key(tid_a, agent, "feedback")
    kb = svc._get_key(tid_b, agent, "feedback")
    assert ka != kb, "tenant_id MUST be in the key to prevent cross-tenant blend"
    assert str(tid_a) in ka
    assert str(tid_b) in kb
    assert str(agent) in ka and str(agent) in kb
