# RBAC Roles

*The role matrix, the write-path enforcement, the exceptions, and the auditing of every role decision.*

## The five roles

Source: `services/gateway/_mw_auth.py:151-158`.

```python
permissions_map = {
    "ADMIN":    ["*"],
    "SECURITY": ["kill_switch", "view_risk", "execute_agent"],
    "AUDITOR":  ["view_risk", "view_audit"],
    "VIEWER":   ["view_risk"],
    "agent":    ["execute_agent"],
}
```

| Role | Intended for | Can read | Can write |
|---|---|---|---|
| `ADMIN` | Tenant administrator | Everything | Everything |
| `SECURITY` | SOC operator | Audit + risk + decision views | Kill switch, write paths, policy edits |
| `AUDITOR` | Compliance auditor | Audit + risk views | None |
| `VIEWER` | Observer / demo account | Risk view only | None |
| `agent` | Programmatic agent | Nothing via reads | `/execute` only |

The role is set at user creation (`services/identity/router.py::create_user`) and embedded in the JWT at every login. Changing a role takes effect at the next login; existing tokens retain their original role until the 15-minute TTL expires.

## Write-path enforcement

Source: `services/gateway/_mw_auth.py:161-169`.

```python
# Write-path enforcement: mutations require ADMIN or SECURITY,
# except agent-role tokens on /execute (controlled by OPA + Decision Engine).
if request.method not in ("GET", "HEAD", "OPTIONS"):
    if role not in ("ADMIN", "SECURITY"):
        if not (is_execute_path and role == "agent"):
            raise HTTPException(
                status_code=403,
                detail="Write operations require ADMIN or SECURITY role",
            )
```

The rule in plain English:

- GET, HEAD, OPTIONS are always allowed (subject to other stages).
- Any POST, PATCH, PUT, DELETE requires `ADMIN` or `SECURITY`.
- One exception: agent-role tokens are allowed POST on `/execute` because the agent role is granted exactly the authority to execute tools (and only on paths matching `is_execute_path`).

This is the rule that produces the `Write operations require ADMIN or SECURITY role` 403 visible on most write surfaces in the UI when an `AUDITOR` or `VIEWER` tries to use a write button.

## The agent role exception

Agent-role tokens carry `role: "agent"` and an embedded `permissions` list. The validator allows the `agent` role to call `/execute` and only `/execute`. Anything else returns 403.

The reasoning: agents are programmatic callers. Letting them mint admin actions would defeat the purpose of having a separate role.

## Per-route role checks

Beyond the global write-path enforcement, several routes apply their own checks:

### Kill switch (decision service)

Source: `services/decision/router.py:79-115`.

`POST /decision/kill-switch/{tenant_id}` and `DELETE` require `ADMIN` or `SECURITY`. Read is `AUDITOR`+. Source: the per-route role gate decorator.

### Policy upload

Source: `services/policy/router.py:436-442`.

`POST /policy/upload`, `/policy/simulate`, `/policy/test` require `ADMIN` or `SECURITY`. A `VIEWER` opening Policy Builder can read the rules but every write button 403s.

### User revoke

Source: `services/identity/router.py:495`.

`POST /auth/revoke` (force-revoke another user's token) requires `ADMIN` or `SECURITY`.

### Platform-admin endpoints

`GET /admin/tenants` requires `ADMIN` AND the `is_platform_admin` flag on the user row. A regular tenant `ADMIN` cannot list other tenants' summaries.

## Roles and tools — the orthogonal axis

A common confusion: an agent's *role* (`agent`) is separate from its *tool grants*. The role authorizes the agent to call `/execute`; the per-agent permissions table authorizes which specific tools the agent may invoke through `/execute`.

```
JWT role:           agent
JWT permissions:    [{"tool_name":"db.query","action":"ALLOW"},
                     {"tool_name":"email.send","action":"ALLOW"}]
```

The role check passes (`agent` is allowed `/execute`). The tool check at stage 4 (policy) verifies the specific `tool_name` against the agent's permission list. Mismatch → 403 with rule_id.

The same orthogonality exists for users: an `ADMIN` user can call `/execute` (because the gateway allows ADMIN to call any path), but only if they include a valid `X-Agent-ID` header pointing at an agent whose permissions cover the request. ADMINs do not have implicit tool-grants of their own.

## What the matrix does NOT cover

- **Object-level permissions.** Aegis does not have row-level grants like "this AUDITOR can read tenant X's audit but not tenant Y's." Tenant isolation is enforced by the JWT's tenant_id; cross-tenant reads are impossible regardless of role.
- **Time-bound role grants.** A user's role is permanent until changed. Temporary role escalation requires a manual role change at the start and end of the window.
- **Fine-grained write permissions.** `SECURITY` and `ADMIN` are coarse. Some platforms split into separate "policy editor", "user editor", "billing editor" roles; Aegis does not.

These are intentional simplifications. Adding axes increases the number of permission combinations to reason about.

## Audit emission on role-relevant actions

Every role-relevant action is audited:

- User role change → `action="user_role_changed"` with `old_role`, `new_role`, `changed_by`.
- Kill switch engaged or disengaged → `action="kill_switch_engaged"` / `"kill_switch_disengaged"` with `engaged_by`, `reason`.
- Policy uploaded → `action="policy_uploaded"` with `name`, `uploaded_by`, `bundle_revision`.
- User token revoked → `action="token_revoked"` with `revoked_by`, `target_user_id`.
- Auto-response rule changed → `action="ar_rule_changed"` with `rule_id`, `changed_by`.

The audit row is the non-repudiable record of who did what when. Combined with the chain integrity guarantees ([Cryptographic Audit Chain](crypto-audit-chain.md)), the role action log is verifiable after the fact.

## SSO and role mapping

When SSO is configured, the IdP's claims are mapped to Aegis roles via the per-tenant `acp:sso_config:{tenant_id}` Redis hash. The mapping is operator-controlled at the Settings → SSO page.

Default behavior: an SSO user with no explicit role mapping is created as `VIEWER`. Operators must escalate via the User Management page or update the SSO mapping to grant higher roles automatically.

## What you should verify in your deployment

1. The five roles are the only roles in use. Grep `acp_identity.users.role` for unexpected values.
2. The platform-admin flag is set on exactly the operator accounts that need cross-tenant visibility.
3. No service has a hard-coded role bypass. Grep for `"ADMIN"` in `services/*` and confirm every hit is a legitimate role check.
4. The audit chain shows a `user_role_changed` row for every recent role escalation.

## Next

- [Identity service](../services/identity.md) — where roles are stored
- [JWT Authentication](jwt-auth.md) — how the role is conveyed
- [Gateway service](../services/gateway.md) — the enforcement point
- [User Management UI](../ui/settings/user-management.md) — the human-facing surface
