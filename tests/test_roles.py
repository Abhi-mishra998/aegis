"""Tests for the canonical Aegis role vocabulary in sdk.common.roles."""
from __future__ import annotations

import pytest

from sdk.common.roles import LEGACY_ROLE_TO_CANONICAL, Role, canonical_role


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Identity projections (already canonical)
        ("OWNER", "OWNER"),
        ("ADMIN", "ADMIN"),
        ("SECURITY_ANALYST", "SECURITY_ANALYST"),
        ("DEVELOPER", "DEVELOPER"),
        ("READ_ONLY", "READ_ONLY"),
        # Legacy → canonical projections
        ("SECURITY", "SECURITY_ANALYST"),
        ("AUDITOR", "READ_ONLY"),
        ("VIEWER", "READ_ONLY"),
        ("AGENT", "DEVELOPER"),
        # Case insensitivity
        ("owner", "OWNER"),
        ("Security", "SECURITY_ANALYST"),
    ],
)
def test_canonical_role_known_mapping(raw, expected):
    assert canonical_role(raw) == expected


def test_canonical_role_unknown_defaults_to_read_only():
    """A misspelled or unknown role must fall to the least-privileged tier."""
    assert canonical_role("ADMINISTATOR_TYPO") == Role.READ_ONLY.value
    assert canonical_role("god") == Role.READ_ONLY.value


def test_canonical_role_falsy_inputs_default_to_read_only():
    assert canonical_role(None) == Role.READ_ONLY.value
    assert canonical_role("") == Role.READ_ONLY.value


def test_role_enum_exposes_all_5_tiers():
    expected = {"OWNER", "ADMIN", "SECURITY_ANALYST", "DEVELOPER", "READ_ONLY"}
    assert {r.value for r in Role} == expected


def test_legacy_map_covers_every_pre_sprint1_value():
    """Every value that exists on a pre-migration user row projects to a real Role."""
    pre_sprint1_values = ("ADMIN", "SECURITY", "AUDITOR", "VIEWER", "AGENT")
    for value in pre_sprint1_values:
        assert value in LEGACY_ROLE_TO_CANONICAL
        assert LEGACY_ROLE_TO_CANONICAL[value] in {r.value for r in Role}
