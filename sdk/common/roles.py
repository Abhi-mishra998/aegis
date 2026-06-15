"""
Canonical Aegis role vocabulary — shared by services/identity (writes the
role onto User rows) and services/gateway (the verify_role middleware
reads it off the JWT claim).

Both services import from here so a rename of `SECURITY_ANALYST` cannot
silently drift between the two.

The migration `f1e2d3c4b5a6_sprint1_clerk_signup_shadow.py` adds the new
enum values to the Postgres `user_role_enum` type. Pre-Sprint-1 rows
carry legacy values (SECURITY/AUDITOR/VIEWER) which are projected onto
the canonical vocabulary at read time via `canonical_role()`.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The 5-tier Aegis role vocabulary (PRODUCT_PLAN.md §1.1)."""

    OWNER = "OWNER"
    ADMIN = "ADMIN"
    SECURITY_ANALYST = "SECURITY_ANALYST"
    DEVELOPER = "DEVELOPER"
    READ_ONLY = "READ_ONLY"


# Legacy enum values (ADMIN/SECURITY/AUDITOR/VIEWER/AGENT) projected onto
# the canonical Sprint-1 vocabulary. Used by canonical_role().
LEGACY_ROLE_TO_CANONICAL: dict[str, str] = {
    # Existing values that already match
    "ADMIN":            Role.ADMIN.value,
    "OWNER":            Role.OWNER.value,
    "SECURITY_ANALYST": Role.SECURITY_ANALYST.value,
    "DEVELOPER":        Role.DEVELOPER.value,
    "READ_ONLY":        Role.READ_ONLY.value,
    # Legacy values that get projected
    "SECURITY":         Role.SECURITY_ANALYST.value,
    "AUDITOR":          Role.READ_ONLY.value,
    "VIEWER":           Role.READ_ONLY.value,
    "AGENT":            Role.DEVELOPER.value,
}


def canonical_role(raw: str | None) -> str:
    """Project any stored or claimed role string onto the Role vocabulary.

    Unknown values fall back to READ_ONLY — the least-privileged tier —
    so a misspelled role can never silently escalate a user's permissions.
    """
    if not raw:
        return Role.READ_ONLY.value
    return LEGACY_ROLE_TO_CANONICAL.get(raw.upper(), Role.READ_ONLY.value)


__all__ = [
    "Role",
    "LEGACY_ROLE_TO_CANONICAL",
    "canonical_role",
]
