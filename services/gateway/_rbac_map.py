"""Sprint EH-1 — Centralised path → required-role map.

Single source of truth for what role is allowed to call which route.
The matching is done in `_authorize_request()` which the auth middleware
calls AFTER token validation but BEFORE dispatch to the route handler.

Conventions:
  - Patterns are matched against `request.url.path` in declaration order
    (first match wins). More-specific patterns MUST come before broader
    ones; the trailing `*` wildcard matches any suffix.
  - The `methods` field is the HTTP verb allow-list for that rule.
    `("*",)` matches every method.
  - `roles` is the set of canonical Aegis roles allowed. Empty set means
    "any authenticated principal" (still requires a valid token).
  - Routes not in this map fall through to the legacy permission_map
    check in `_mw_auth.py` (which is `["*"]` for OWNER/ADMIN — wide-open
    historical behaviour). Anything new should ALWAYS be added here.

The canonical spec is docs/security/rbac_matrix.md. Keep the two in sync.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# Canonical Aegis role tier (highest → lowest).
ROLE_TIERS = ("OWNER", "ADMIN", "SECURITY_ANALYST", "DEVELOPER", "READ_ONLY")


def _meets(actual: str, minimum: str) -> bool:
    """True iff ``actual`` is at-least ``minimum`` in the tier hierarchy."""
    if actual not in ROLE_TIERS or minimum not in ROLE_TIERS:
        return False
    return ROLE_TIERS.index(actual) <= ROLE_TIERS.index(minimum)


@dataclass(frozen=True)
class Rule:
    pattern: str            # glob: `*` matches one path segment; trailing `*` matches any suffix
    methods: tuple[str, ...]  # ("GET",) or ("*",)
    roles: frozenset[str]   # explicit allow-set, OR
    min_role: str | None    # tier-based minimum (read-allows-all-above)
    _regex: "re.Pattern[str] | None" = None  # filled on first match()

    def matches(self, path: str, method: str) -> bool:
        if "*" not in self.methods and method.upper() not in self.methods:
            return False
        # Drop query string from path before pattern match.
        path = path.split("?", 1)[0]
        # Convert pattern into regex: `*` -> any non-slash segment (or any
        # suffix when at the end of the pattern). This lets us write rules
        # like `/agents/*/quarantine` that match `/agents/abc/quarantine`
        # but not `/agents/abc`.
        if not self._regex:
            object.__setattr__(self, "_regex", _compile(self.pattern))
        return self._regex.match(path) is not None


def _compile(pattern: str) -> "re.Pattern[str]":
    if pattern.endswith("*"):
        # Trailing wildcard = any suffix
        prefix = pattern[:-1]
        regex = re.escape(prefix).replace(r"\*", r"[^/]+") + r".*"
    else:
        regex = re.escape(pattern).replace(r"\*", r"[^/]+")
    return re.compile(rf"^{regex}$")


def _R(pattern: str, methods: Iterable[str], *, roles: Iterable[str] = (), min_role: str | None = None) -> Rule:
    return Rule(
        pattern=pattern,
        methods=tuple(m.upper() for m in methods),
        roles=frozenset(r.upper() for r in roles),
        min_role=min_role.upper() if min_role else None,
    )


# Order matters: more-specific first.
RULES: tuple[Rule, ...] = (
    # ── Workspace + identity ────────────────────────────────────────────
    _R("/workspace/system-values",     ("PATCH",),       roles=("OWNER",)),
    _R("/workspace/exit-shadow-mode",  ("POST",),        roles=("OWNER",)),
    _R("/workspace/apply-preset",      ("POST",),        roles=("OWNER",)),
    _R("/workspace/slack-config",      ("PUT",),         roles=("OWNER", "ADMIN")),
    _R("/workspace/slack-config",      ("GET",),         min_role="ADMIN"),
    _R("/workspace/policy-packs",      ("PUT",),         roles=("OWNER", "ADMIN")),
    _R("/workspace/*",                 ("GET",),         min_role="READ_ONLY"),
    _R("/auth/users",                  ("POST",),        roles=("OWNER",)),
    _R("/auth/users/*",                ("DELETE",),      roles=("OWNER",)),
    _R("/auth/users/*",                ("PATCH",),       roles=("OWNER", "ADMIN")),
    _R("/auth/users*",                 ("GET",),         min_role="ADMIN"),
    _R("/auth/sso/config",             ("POST", "PUT"),  roles=("OWNER",)),
    _R("/auth/sso/config*",            ("GET",),         min_role="ADMIN"),
    _R("/auth/tenants/*",              ("*",),           min_role="ADMIN"),
    _R("/auth/me",                     ("GET",),         min_role="READ_ONLY"),

    # ── Agents ──────────────────────────────────────────────────────────
    _R("/agents/*/quarantine",         ("POST",),        min_role="SECURITY_ANALYST"),
    _R("/agents/*/release",            ("POST",),        min_role="SECURITY_ANALYST"),
    _R("/agents/*/permissions",        ("PUT",),         roles=("OWNER", "ADMIN")),
    _R("/agents/*/permissions",        ("GET",),         min_role="READ_ONLY"),
    _R("/agents",                      ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/agents/*",                    ("PATCH",),       roles=("OWNER", "ADMIN")),
    _R("/agents/*",                    ("DELETE",),      roles=("OWNER",)),
    _R("/agents*",                     ("GET",),         min_role="READ_ONLY"),
    _R("/registry/onboarding*",        ("*",),           roles=("OWNER", "ADMIN")),

    # ── Decisions + execution ──────────────────────────────────────────
    _R("/execute",                     ("POST",),        min_role="DEVELOPER"),
    _R("/decision/*",                  ("GET",),         min_role="READ_ONLY"),

    # ── Audit + compliance + forensics + storylines + iag + incidents
    _R("/audit/logs/export",           ("POST",),        min_role="SECURITY_ANALYST"),
    _R("/audit/logs/verify",           ("GET",),         min_role="READ_ONLY"),
    _R("/audit/logs/search",           ("POST",),        min_role="READ_ONLY"),
    _R("/audit/logs*",                 ("GET",),         min_role="READ_ONLY"),
    _R("/audit*",                      ("GET",),         min_role="READ_ONLY"),
    _R("/compliance/export",           ("POST",),        roles=("OWNER",)),
    _R("/compliance/*",                ("GET",),         min_role="SECURITY_ANALYST"),
    _R("/forensics/*",                 ("*",),           min_role="SECURITY_ANALYST"),
    _R("/storylines*",                 ("*",),           min_role="SECURITY_ANALYST"),
    _R("/iag/*",                       ("GET",),         min_role="SECURITY_ANALYST"),
    _R("/incidents/*",                 ("PATCH", "POST", "DELETE"), min_role="SECURITY_ANALYST"),
    _R("/incidents*",                  ("GET",),         min_role="READ_ONLY"),
    _R("/replay/*",                    ("GET",),         min_role="READ_ONLY"),

    # ── Operations + integrations ──────────────────────────────────────
    _R("/dashboard/*",                 ("GET",),         min_role="READ_ONLY"),
    _R("/notifications/*",             ("POST",),        min_role="READ_ONLY"),
    _R("/notifications*",              ("GET",),         min_role="READ_ONLY"),
    _R("/api-keys/*",                  ("DELETE",),      roles=("OWNER", "ADMIN")),
    _R("/api-keys",                    ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/api-keys*",                   ("GET",),         roles=("OWNER", "ADMIN")),
    _R("/team/employees/*",            ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/team/employees",              ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/team*",                       ("GET",),         min_role="READ_ONLY"),
    _R("/webhooks/test/*",             ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/webhooks/config",             ("PUT", "POST"),  roles=("OWNER", "ADMIN")),
    _R("/webhooks/config",             ("GET",),         roles=("OWNER", "ADMIN")),
    _R("/siem/*",                      ("*",),           roles=("OWNER", "ADMIN")),
    _R("/sso/slack/*",                 ("*",),           roles=("OWNER", "ADMIN")),
    # Sprint EI-2 — Jira ITSM integration. The api_token is per-tenant; only
    # OWNER + ADMIN can set or delete it. READ_ONLY can see has_api_token: true
    # but never the token itself.
    _R("/integrations/jira/test",      ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/integrations/jira",           ("PUT", "DELETE"), roles=("OWNER", "ADMIN")),
    _R("/integrations/jira",           ("GET",),         min_role="READ_ONLY"),
    # Sprint EI-6 — ServiceNow ITSM integration. Same authz as Jira.
    _R("/integrations/servicenow/test", ("POST",),       roles=("OWNER", "ADMIN")),
    _R("/integrations/servicenow",     ("PUT", "DELETE"), roles=("OWNER", "ADMIN")),
    _R("/integrations/servicenow",     ("GET",),         min_role="READ_ONLY"),
    _R("/integrations*",               ("GET",),         min_role="READ_ONLY"),
    # Sprint EI-3 — SCIM bearer token management (Okta provisioning). OWNER-only:
    # issuance returns plaintext exactly once and a leaked SCIM token grants
    # full directory write to the entire tenant. /scim/v2/tokens is JWT-gated
    # by this rule; /scim/v2/{Users,Groups,...} is skip-listed in middleware
    # and uses its own scim_ bearer validation.
    _R("/scim/v2/tokens/*",            ("DELETE",),      roles=("OWNER",)),
    _R("/scim/v2/tokens",              ("POST",),        roles=("OWNER",)),
    _R("/scim/v2/tokens",              ("GET",),         roles=("OWNER",)),
    _R("/billing/checkout",            ("POST",),        roles=("OWNER",)),
    _R("/billing/portal",              ("POST",),        roles=("OWNER",)),
    _R("/billing*",                    ("GET",),         roles=("OWNER",)),
    _R("/tenant/quota",                ("GET",),         min_role="READ_ONLY"),
    _R("/auto-response/*",             ("*",),           min_role="SECURITY_ANALYST"),
    _R("/autonomy/contracts/*",        ("POST", "PATCH", "DELETE"), roles=("OWNER", "ADMIN")),
    _R("/autonomy/contracts*",         ("POST",),        roles=("OWNER", "ADMIN")),
    _R("/autonomy/contracts*",         ("GET",),         min_role="READ_ONLY"),
    _R("/autonomy/overrides*",         ("*",),           min_role="SECURITY_ANALYST"),
    _R("/autonomy/playbooks/*",        ("POST",),        min_role="SECURITY_ANALYST"),
    _R("/autonomy/playbooks*",         ("*",),           min_role="SECURITY_ANALYST"),
    _R("/kill-switch*",                ("POST",),        roles=("OWNER", "SECURITY_ANALYST")),
    _R("/admin*",                      ("*",),           roles=("OWNER",)),
    _R("/threat-intel*",               ("*",),           min_role="SECURITY_ANALYST"),
    _R("/remediation*",                ("*",),           min_role="SECURITY_ANALYST"),
    _R("/system/values*",              ("GET",),         min_role="READ_ONLY"),
)


def role_required(path: str, method: str) -> tuple[bool, str] | None:
    """Return (allowed_for_any_role_above_X, reason) for documentation,
    or None if no rule matches the path/method.

    For an actual authorization decision use `is_authorized()`.
    """
    for r in RULES:
        if r.matches(path, method):
            if r.roles:
                return True, f"explicit allow-list {sorted(r.roles)}"
            if r.min_role:
                return True, f"min role {r.min_role}"
            return True, "authenticated"
    return None


def is_authorized(path: str, method: str, actual_role: str) -> tuple[bool, str | None]:
    """Decide if ``actual_role`` is allowed to call ``method path``.

    Returns (allowed, denial_reason_or_None).

    Routes not covered by any rule are permitted (fall-through to legacy
    permission_map). This keeps the change low-risk while we migrate.
    """
    actual = (actual_role or "").upper()
    for r in RULES:
        if not r.matches(path, method):
            continue
        if r.roles and actual in r.roles:
            return True, None
        if r.min_role and _meets(actual, r.min_role):
            return True, None
        if not r.roles and not r.min_role:
            return True, None
        if r.roles:
            return False, f"role {actual!r} not in {sorted(r.roles)} for {method} {path}"
        return False, f"role {actual!r} below required min {r.min_role!r} for {method} {path}"
    return True, None  # uncovered → allow (legacy fall-through)
