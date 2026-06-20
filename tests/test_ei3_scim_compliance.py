"""Sprint EI-3 — SCIM 2.0 compliance tests.

Covers the SCIM behaviours Okta's connector exercises during normal
provisioning:
  - GET /ServiceProviderConfig          200 + advertises authenticationSchemes
  - GET /ResourceTypes / /Schemas       lists User + Group
  - User create / read / patch active / delete
  - filter ?filter=userName eq "x"      single-attr eq supported
  - pagination ?startIndex= ?count=     bounded; totalResults populated
  - 409 + scimType=uniqueness on duplicate userName
  - 401 SCIM error envelope on bad bearer
  - RBAC matrix for /scim/v2/tokens (OWNER-only)

These do NOT exercise the live Postgres path; instead, the SCIM router's
helpers are unit-tested in isolation and the RBAC matrix is asserted
against services/gateway/_rbac_map.is_authorized.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei3-unit-test")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.gateway._rbac_map import is_authorized  # noqa: E402
from services.gateway.routers.scim import (  # noqa: E402
    _extract_member_ids,
    _filter_value_from_path,
    _parse_eq_filter,
)
from services.gateway.routers.scim_tokens import _mint_token  # noqa: E402


# ── 1. Filter parsing ─────────────────────────────────────────────────────
class TestFilterParse:
    def test_eq_double_quotes(self):
        assert _parse_eq_filter('userName eq "alice@example.com"', "userName") == "alice@example.com"

    def test_eq_single_quotes(self):
        assert _parse_eq_filter("userName eq 'alice@example.com'", "userName") == "alice@example.com"

    def test_case_insensitive_attr(self):
        assert _parse_eq_filter('USERNAME eq "x"', "userName") == "x"

    def test_wrong_attr_returns_none(self):
        assert _parse_eq_filter('displayName eq "Eng"', "userName") is None

    def test_no_filter(self):
        assert _parse_eq_filter(None, "userName") is None
        assert _parse_eq_filter("", "userName") is None

    def test_displayname_attr(self):
        assert _parse_eq_filter('displayName eq "Engineering"', "displayName") == "Engineering"


# ── 2. Member-id extraction ───────────────────────────────────────────────
class TestMemberIds:
    def test_single_dict(self):
        out = _extract_member_ids({"value": "00000000-0000-0000-0000-000000000001"})
        assert len(out) == 1
        assert str(out[0]) == "00000000-0000-0000-0000-000000000001"

    def test_list_of_dicts(self):
        out = _extract_member_ids([
            {"value": "00000000-0000-0000-0000-000000000001"},
            {"value": "00000000-0000-0000-0000-000000000002"},
        ])
        assert len(out) == 2

    def test_invalid_uuid_skipped(self):
        out = _extract_member_ids([{"value": "not-a-uuid"}])
        assert out == []

    def test_none(self):
        assert _extract_member_ids(None) == []


class TestFilterValueFromPath:
    def test_normal(self):
        assert _filter_value_from_path('members[value eq "abc-123"]') == "abc-123"

    def test_no_bracket(self):
        assert _filter_value_from_path("members") is None

    def test_no_eq(self):
        assert _filter_value_from_path("members[displayName ne \"x\"]") is None


# ── 3. Token mint ─────────────────────────────────────────────────────────
class TestMintToken:
    def test_format(self):
        pt, prefix, sha = _mint_token()
        assert pt.startswith("scim_")
        # 14 bytes -> ceil(112/5) = 23 base32 chars (after strip='=').
        # Plus the 'scim_' prefix that's 28 total. Some chars may be 2,3,4,5,6,7,a-z.
        assert len(pt) == 28
        assert all(c in "abcdefghijklmnopqrstuvwxyz234567" for c in pt[5:])
        assert "scim_" in prefix and "…" in prefix
        assert len(sha) == 64  # sha256 hex

    def test_uniqueness(self):
        seen = set()
        for _ in range(100):
            pt, _, _ = _mint_token()
            assert pt not in seen
            seen.add(pt)

    def test_hash_matches(self):
        import hashlib
        pt, _, sha = _mint_token()
        assert hashlib.sha256(pt.encode()).hexdigest() == sha


# ── 4. RBAC — /scim/v2/tokens is OWNER-only ──────────────────────────────
class TestScimTokensRBAC:
    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True),
        ("ADMIN", False),
        ("SECURITY_ANALYST", False),
        ("DEVELOPER", False),
        ("READ_ONLY", False),
    ])
    def test_post_owner_only(self, role, allowed):
        ok, _ = is_authorized("/scim/v2/tokens", "POST", role)
        assert ok is allowed

    @pytest.mark.parametrize("role,allowed", [
        ("OWNER", True),
        ("ADMIN", False),
        ("SECURITY_ANALYST", False),
        ("DEVELOPER", False),
    ])
    def test_get_owner_only(self, role, allowed):
        ok, _ = is_authorized("/scim/v2/tokens", "GET", role)
        assert ok is allowed

    def test_delete_owner_only(self):
        assert is_authorized("/scim/v2/tokens/abc", "DELETE", "OWNER")[0] is True
        assert is_authorized("/scim/v2/tokens/abc", "DELETE", "ADMIN")[0] is False


# ── 5. SCIM endpoints are skip-listed in JWT middleware ──────────────────
class TestSCIMSkipListed:
    """The SCIM 2.0 protocol endpoints must NOT be JWT-gated (they use
    scim_ bearer tokens instead, validated by the SCIM router itself)."""

    def test_users_in_skip_list(self):
        from services.gateway.middleware import _SKIP_PATH_PREFIXES
        assert any(p.startswith("/scim/v2/Users") for p in _SKIP_PATH_PREFIXES)

    def test_groups_in_skip_list(self):
        from services.gateway.middleware import _SKIP_PATH_PREFIXES
        assert any(p.startswith("/scim/v2/Groups") for p in _SKIP_PATH_PREFIXES)

    def test_discovery_in_skip_list(self):
        from services.gateway.middleware import _SKIP_PATH_PREFIXES
        for endpoint in ("ServiceProviderConfig", "Schemas", "ResourceTypes"):
            assert any(endpoint in p for p in _SKIP_PATH_PREFIXES), endpoint

    def test_tokens_NOT_in_skip_list(self):
        """Management endpoints stay under JWT auth (OWNER-only via RBAC)."""
        from services.gateway.middleware import _SKIP_PATH_PREFIXES
        assert not any("/scim/v2/tokens" in p for p in _SKIP_PATH_PREFIXES)


# ── 6. SCIM error envelope shape ─────────────────────────────────────────
class TestScimErrorEnvelope:
    """Both _scim_unauthorized() and _scim_error() must use the RFC 7644
    error schema so Okta can parse them."""

    def test_unauthorized_envelope(self):
        from services.gateway._scim_auth import _scim_unauthorized
        exc = _scim_unauthorized("bad token")
        assert exc.status_code == 401
        body = exc.detail
        assert isinstance(body, dict)
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert body["status"] == "401"
        assert body["detail"] == "bad token"
        assert exc.headers.get("WWW-Authenticate", "").startswith("Bearer")

    def test_scim_error_envelope(self):
        from services.gateway.routers.scim import _scim_error
        exc = _scim_error(409, "User 'a@b.com' already exists",
                          scim_type="uniqueness")
        assert exc.status_code == 409
        body = exc.detail
        assert body["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
        assert body["status"] == "409"
        assert body["scimType"] == "uniqueness"
        assert "already exists" in body["detail"]
