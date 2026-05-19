# Cross-Service Import Audit — 2026-05-16

Audit of Python `from services.X import ...` statements that bypass the HTTP
service boundary. Each violation is classified and assigned a disposition.

---

## Classification Legend

| Class | Meaning | Required action |
|---|---|---|
| `sdk-shared-ok` | Both services are in the same runtime OR the import is from `sdk/`, which is the designated shared-code layer | No change needed |
| `http-required` | Two separate microservices with separate DB users, ports, and containers. Must communicate via HTTP | Refactor to HTTP on next sprint |
| `co-located-ok` | The importing module is in the same service package (e.g. `behavior` importing `behavior`) | No change needed |

---

## Violations Found

### 1. `services/behavior/service.py:28`
```python
from services.behavior.intelligence import ...  # co-located, same package
```
**Disposition**: `co-located-ok` — intelligence module is part of the behavior package.

### 2. `services/behavior/service.py:29,285`
```python
from services.learning.service import LearningService  # cross-service
```
**Disposition**: `http-required` — behavior and learning are separate Docker
containers (`acp_behavior`, `acp_learning`) with separate DB users. The learning
engine call is invoked inline on the hot path. Should be an HTTP call to
`/learning/profile` or moved to a shared in-process cache.

**Risk**: Any import-time failure in learning prevents behavior service startup.
**Priority**: Week 2 — replace with HTTP client call + 200ms timeout.

### 3. `services/behavior/service.py:64`
```python
from services.usage.schemas import ...  # cross-service schema import
```
**Disposition**: `http-required` — usage and behavior are separate services.
Schema sharing via import creates a hidden compile-time coupling.
**Mitigation in use**: Only imports a Pydantic schema, not a DB session. Low blast
radius but coupling remains.
**Priority**: Week 2 — duplicate the schema inline or move to `sdk/common/schemas/`.

### 4. `services/billing/router.py:18`
```python
from services.usage.models import UsageRecord  # cross-service model
```
**Disposition**: `http-required` — direct SQLAlchemy model import from a sibling
service's DB layer. This would fail if billing and usage are ever on different hosts.
**Priority**: Week 3 — billing should call usage service HTTP endpoint.

### 5. `services/forensics/router.py:10`
```python
from services.audit.models import AuditLog  # cross-service model
```
**Disposition**: `http-required` — forensics queries the audit DB directly via
SQLAlchemy model import instead of calling `GET /audit/logs`. This bypasses the
audit service's access control layer.
**Priority**: Week 2 — replace with HTTP call to `/audit/logs?agent_id=...`.

### 6. `services/gateway/middleware.py:42`
```python
from services.decision.schemas import Decision, ExecutionAction
```
**Disposition**: `sdk-shared-ok` (with caveat) — gateway and decision are separate
services, but this import is for Pydantic **schema** types only (no DB access, no
business logic). The same schema is already duplicated in `sdk/common/`. Moving
Decision/ExecutionAction to `sdk/common/schemas.py` would eliminate this coupling.
**Priority**: Week 3 — relocate Decision/ExecutionAction to sdk/common.

### 7. `services/gateway/main.py:297`
```python
from services.policy.local_eval import evaluate
```
**Disposition**: `http-required` — gateway calls policy logic inline as a fast
path (JWT-embedded claims, no round trip). This is a documented optimization
(`# Fast-path: gateway embedded JWT agent_claims → evaluate locally`). The tradeoff
is intentional: it avoids one HTTP hop on the 99th percentile hot path.
**Recommendation**: Document as intentional fast-path in README.md; keep for now.

### 8. `services/gateway/client.py:428`
```python
from services.policy.schemas import EvaluationRequest
```
**Disposition**: `sdk-shared-ok` (same caveat as #6) — schema-only import.
Relocate to `sdk/common/schemas.py` in Week 3.

### 9. `services/usage/main.py:41`
```python
from services.audit.models import AuditLog, PendingUsageEvent
```
**Disposition**: `http-required` — usage service queries the audit DB directly
to drain `pending_usage_events`. This is the transactional outbox worker pattern;
the outbox table lives in the audit DB by design (atomicity with audit writes).
**Exception granted**: The outbox worker is the *only* writer-consumer of
`pending_usage_events` and must run transactionally. Keeping the direct import is
acceptable here. Document this exception explicitly.

### 10. `services/usage/main.py:17,241`
```python
from services.billing.router import ...
```
**Disposition**: `http-required` — usage importing billing logic inline.
**Priority**: Week 3 — usage should call billing service HTTP endpoint.

---

## Summary

| Count | Class | Action |
|---|---|---|
| 2 | `co-located-ok` | None |
| 2 | `sdk-shared-ok` (schema only) | Move to sdk/common in Week 3 |
| 1 | Intentional fast-path (documented exception) | Document in README.md |
| 1 | Transactional outbox exception | Document in README.md |
| 4 | `http-required` — refactor required | Week 2–3 |

---

## Async Safety Inventory

Bare `asyncio.create_task()` calls that lacked `_safe_bg` wrapping at audit time:

| File | Line | Status |
|---|---|---|
| `services/learning/service.py` | 157 | ✅ FIXED — wrapped with `_safe_bg` (commit 584b2e5) |
| `services/gateway/client.py` | 277 | ✅ FIXED — wrapped with `_safe_bg` (commit 584b2e5) |

All other `create_task` calls are either:
- Assigned to a named variable (lifespan tasks in `identity_graph/main.py:31-33`)
- Already using the existing `_safe_bg` wrapper from their module

---

## Corrections to PRE_SPRINT_STATE.md

`PRE_SPRINT_STATE.md` stated signal thresholds as: behavior=0.4, anomaly=0.5, cost=0.7.
The authoritative source is `services/decision/findings.py::SIGNAL_THRESHOLDS`:

| Signal | PRE_SPRINT_STATE (wrong) | Code (correct) |
|---|---|---|
| inference | 0.6 | 0.6 ✓ |
| behavior | 0.4 | **0.60** |
| anomaly | 0.5 | **0.70** |
| cost | 0.7 | **0.50** |
| cross_agent | 0.4 | 0.4 ✓ |

These thresholds were updated in Sprint 2.2 (2026-05-15) when findings vocabulary
was introduced. The PRE_SPRINT_STATE report referenced the old inline thresholds.

---

Generated: 2026-05-16  
Branch: audit-fixes-r1  
