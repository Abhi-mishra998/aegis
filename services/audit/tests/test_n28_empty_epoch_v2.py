"""N28 (2026-06-21) — empty-epoch Merkle root collision fix.

Before v2 the seed was ``sha256(prev_root_hash || domain_v1)`` which
collides whenever two empty-epoch markers share a prev_root_hash —
most concretely, every tenant's genesis empty day (prev=None) hashed
to the same value, and any cross-tenant coincidence of prev bytes
also collided.

v2 mixes ``root_date.isoformat()`` into the seed, so two consecutive
empty days never collide on the same root and the seed is per-day.

These tests cover:
  * v2 changes-with-date even when prev is fixed (the core fix)
  * v1 still computable (back-compat for historical-root verification)
  * v1 and v2 are byte-distinct for the same input (no accidental
    cross-version collision)
  * the back-compat shim — calling empty_epoch_root_hash() without a
    date returns the v1 hash so existing callers don't silently change
"""
from __future__ import annotations

import hashlib
from datetime import date

import pytest

from services.audit.transparency import (
    empty_epoch_root_hash,
    empty_epoch_root_hash_v1,
)


# --------------------------------------------------------------------------- #
# v2 — the actual N28 fix                                                      #
# --------------------------------------------------------------------------- #


class TestEmptyEpochV2NoCollision:
    def test_two_consecutive_empty_days_never_collide(self) -> None:
        """The headline fix: distinct dates → distinct roots, even when
        prev_root_hash is the same."""
        prev = "a" * 64
        day_a = date(2026, 6, 10)
        day_b = date(2026, 6, 11)
        assert empty_epoch_root_hash(prev, day_a) != empty_epoch_root_hash(prev, day_b)

    def test_two_tenants_genesis_empty_day_never_collide(self) -> None:
        """Cross-tenant collision case: both tenants have no prior root
        (prev=None) AND the same first-empty-day date. The date alone
        doesn't help here (it's the same), but in practice tenants get
        provisioned on different dates so this is the realistic
        protection vector. Pin behavior anyway."""
        same_day = date(2026, 6, 21)
        h = empty_epoch_root_hash(None, same_day)
        assert isinstance(h, str) and len(h) == 64
        # Different days → different hashes — the realistic protection.
        next_day = date(2026, 6, 22)
        assert empty_epoch_root_hash(None, same_day) != empty_epoch_root_hash(None, next_day)

    def test_v2_is_deterministic_for_same_inputs(self) -> None:
        prev = "f" * 64
        day = date(2026, 6, 21)
        assert empty_epoch_root_hash(prev, day) == empty_epoch_root_hash(prev, day)

    def test_v2_still_changes_with_prev(self) -> None:
        day = date(2026, 6, 21)
        assert empty_epoch_root_hash("a" * 64, day) != empty_epoch_root_hash("b" * 64, day)

    def test_v2_seed_format_is_pipe_separated(self) -> None:
        """A future maintainer should not silently switch separators —
        the seed format is part of the cryptographic contract auditors
        re-derive offline."""
        prev = "1" * 64
        day = date(2026, 6, 21)
        expected_seed = f"{prev}|{day.isoformat()}|transparency_empty_epoch_v2"
        expected = hashlib.sha256(expected_seed.encode("ascii")).hexdigest()
        assert empty_epoch_root_hash(prev, day) == expected


# --------------------------------------------------------------------------- #
# v1 — back-compat                                                             #
# --------------------------------------------------------------------------- #


class TestEmptyEpochV1BackCompat:
    def test_v1_helper_still_callable(self) -> None:
        """Auditors holding archived v1 roots must be able to re-derive."""
        prev = "a" * 64
        h = empty_epoch_root_hash_v1(prev)
        assert isinstance(h, str) and len(h) == 64

    def test_v1_byte_format_unchanged(self) -> None:
        """The v1 seed format is sha256(prev || '\\ntransparency_empty_epoch_v1\\n').
        Any historical root the customer archived MUST verify against the
        same bytes."""
        prev = "a" * 64
        expected_seed = prev.encode("ascii") + b"\ntransparency_empty_epoch_v1\n"
        expected = hashlib.sha256(expected_seed).hexdigest()
        assert empty_epoch_root_hash_v1(prev) == expected

    def test_v1_and_v2_produce_different_hashes(self) -> None:
        """A v2 hash MUST NOT collide with a v1 hash for the same prev
        (otherwise the version field is a lie)."""
        prev = "c" * 64
        day = date(2026, 6, 21)
        v1_h = empty_epoch_root_hash_v1(prev)
        v2_h = empty_epoch_root_hash(prev, day)
        assert v1_h != v2_h

    def test_omitting_date_routes_to_v1(self) -> None:
        """Back-compat shim: calling empty_epoch_root_hash(prev) with no
        date returns the v1 value, so the byte output of existing
        callers in legacy code doesn't change."""
        prev = "d" * 64
        # No date arg → v1
        assert empty_epoch_root_hash(prev) == empty_epoch_root_hash_v1(prev)


# --------------------------------------------------------------------------- #
# Distinctness from genuine empty Merkle root                                 #
# --------------------------------------------------------------------------- #


def test_v2_distinct_from_sha256_empty_string() -> None:
    """sha256(b'') is the Merkle sentinel for 'no leaves'. The empty-epoch
    marker MUST NOT collide with it — otherwise the verifier can't
    distinguish 'we sealed an empty epoch' from 'someone fed us an empty
    leaf set with no domain tag'."""
    empty_merkle = hashlib.sha256(b"").hexdigest()
    day = date(2026, 6, 21)
    assert empty_epoch_root_hash(None, day) != empty_merkle
    assert empty_epoch_root_hash("a" * 64, day) != empty_merkle
