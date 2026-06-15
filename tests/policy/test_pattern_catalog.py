"""Sprint 8 — pattern catalog shape tests."""
from __future__ import annotations

from services.policy import pattern_catalog as pc


def test_exfil_hosts_nonempty_sorted_unique():
    assert len(pc.EXFIL_HOSTS) > 0
    assert list(pc.EXFIL_HOSTS) == sorted(set(pc.EXFIL_HOSTS))
    assert all(isinstance(v, str) for v in pc.EXFIL_HOSTS)
    assert all(v == v.lower() for v in pc.EXFIL_HOSTS), \
        "EXFIL_HOSTS must be lowercase for case-insensitive substring match"


def test_offshore_tokens_nonempty_sorted_unique():
    assert len(pc.OFFSHORE_TOKENS) > 0
    assert list(pc.OFFSHORE_TOKENS) == sorted(set(pc.OFFSHORE_TOKENS))
    assert all(isinstance(v, str) for v in pc.OFFSHORE_TOKENS)
    assert all(v == v.lower() for v in pc.OFFSHORE_TOKENS)


def test_curated_known_pastebin_host_present():
    """Smoke check — `transfer.sh` is the canonical example in every
    threat-intel deck. Removing it from EXFIL_HOSTS should require a
    deliberate code review, not slip past."""
    assert "transfer.sh" in pc.EXFIL_HOSTS
    assert "pastebin.com" in pc.EXFIL_HOSTS


def test_canonical_module_uses_the_same_constants():
    """Defends against a future refactor that re-introduces an inline
    copy of these lists in canonical.py."""
    from services.policy import canonical
    assert canonical._KNOWN_EXFIL_DESTS is pc.EXFIL_HOSTS
    assert canonical._OFFSHORE_TOKENS is pc.OFFSHORE_TOKENS
