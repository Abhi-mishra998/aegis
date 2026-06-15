"""A7 — networking-disabled verifier test.

The product promise (final-sprint.md v3 §"The one sentence every sprint
serves"):

    "Don't trust us. Download the bundle, run the open verifier, prove
     the record wasn't altered — offline, no Aegis account, no API key,
     no network call."

If the verifier ever attempted a network call, that promise would be
broken: a Big-4 audit firm would have to trust that the call didn't
phone home with private data, didn't depend on a vendor endpoint being
reachable, didn't validate a key against a remote KMS. The whole
"vendor-trustless verification" premise of AEVF collapses.

This test enforces the promise empirically. We monkey-patch
`socket.socket` so any attempt to construct a socket raises
`PermissionError`, then run the full verifier against the reference
evidence package. The verifier must still report PASS — because it has
no business touching the network in the first place.

Run:
    pytest tools/aegis_verify/tests/test_no_network.py -v
"""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from aegis_verify.verifier import verify_bundle


REPO_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_BUNDLE = REPO_ROOT / "docs" / "AEVF" / "reference-bundle-2026-06.json"


class _NetworkDisabled(PermissionError):
    """Raised the moment the verifier attempts to construct a socket."""


@pytest.fixture
def network_disabled(monkeypatch):
    """Block every attempt to create a TCP/UDP/Unix socket.

    Implementation: replace the `socket.socket` class with a no-op stub
    that raises immediately on construction. We also block
    `socket.create_connection`, `socket.getaddrinfo`, and `socket.gethostbyname`
    so DNS lookups can't slip through either.

    Any verifier line that tries to reach the network — directly via
    `socket`, via `httpx`/`requests`, via `urllib`, via the `cryptography`
    library, via *anything* — will fail loudly with `_NetworkDisabled`.
    """
    def _no_socket(*args: Any, **kwargs: Any):
        raise _NetworkDisabled(
            "AEVF v0.1.0 §18.4 — the verifier MUST run with networking "
            "disabled. A network attempt was caught by the test harness."
        )

    def _no_lookup(*args: Any, **kwargs: Any):
        raise _NetworkDisabled(
            "AEVF v0.1.0 §18.4 — DNS lookup attempted by the verifier "
            "with networking disabled."
        )

    # Block socket construction.
    monkeypatch.setattr(socket, "socket", _no_socket)
    # Block the convenience helpers that may bypass socket() entirely.
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket, "getaddrinfo", _no_lookup)
    monkeypatch.setattr(socket, "gethostbyname", _no_lookup)
    monkeypatch.setattr(socket, "gethostbyname_ex", _no_lookup)
    # Some libraries cache resolvers via `socket.getaddrinfo`; also patch
    # the symbol exposed from the underlying `_socket` C module so
    # bypasses via direct import fail too.
    if "_socket" in sys.modules:
        try:
            monkeypatch.setattr(sys.modules["_socket"], "socket", _no_socket, raising=False)
        except AttributeError:
            pass


def _load_bundle() -> dict[str, Any]:
    if not REFERENCE_BUNDLE.exists():
        pytest.skip(
            f"reference bundle missing at {REFERENCE_BUNDLE}; regenerate "
            f"with `python3 scripts/aevf/build_reference_bundle.py`"
        )
    return json.loads(REFERENCE_BUNDLE.read_text())


def test_verifier_runs_offline(network_disabled):
    """The verifier MUST verify the reference bundle without any network.

    The reference bundle is shipped in the repo. With sockets disabled,
    the verifier must still load it, run V1-V6, and report PASS.
    """
    bundle = _load_bundle()
    report = verify_bundle(bundle)
    assert report.passed, (
        "verifier did NOT pass offline — this breaks the AEVF spec §18.4 "
        f"promise. Report:\n{report.render(verbose=True)}"
    )
    # All six checks individually:
    expected = {
        "V1_bundle_format_recognized",
        "V2_event_hash_recompute",
        "V3_prev_hash_chain_per_shard",
        "V4_merkle_root_signatures",
        "V5_prev_root_hash_chain",
        "V6_retention_metadata_consistent",
    }
    seen = {c.name for c in report.checks}
    assert expected.issubset(seen), (
        f"missing checks: {expected - seen}; saw {seen}"
    )
    for c in report.checks:
        assert c.passed, f"check {c.name} failed offline: {c.detail}"


def test_network_block_is_actually_enforced(network_disabled):
    """Sanity-check the test harness — proves the network block fires.

    Without this, a regression where the verifier sneaks in a network
    call would not be caught by `test_verifier_runs_offline` (because
    sockets would silently work and the verifier would still pass). By
    asserting that a manual socket attempt raises, we prove the
    harness is actually blocking — so the verifier passing under
    `network_disabled` means it really IS offline-safe.
    """
    with pytest.raises(_NetworkDisabled):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    with pytest.raises(_NetworkDisabled):
        socket.create_connection(("example.invalid", 443))

    with pytest.raises(_NetworkDisabled):
        socket.getaddrinfo("example.invalid", 443)


def test_verifier_imports_no_network_libraries():
    """The verifier module MUST NOT import any network library at module
    load time. Catching this here means a future code-change that adds
    `import httpx` or `import requests` to the verifier surfaces in CI
    instead of breaking auditors at runtime.
    """
    from aegis_verify import verifier as _v

    forbidden = {
        "httpx", "requests", "urllib3", "aiohttp",
        # urllib stdlib is allowed at module top-level (it does not
        # touch the network until a request method is called), but the
        # verifier should not import urllib.request directly.
        "urllib.request",
    }
    # Look at the module's imports via dis-traversal would be over-
    # engineered; the actually-imported set is what counts.
    imported = set(sys.modules)
    leaked = forbidden & imported
    if leaked:
        # Only flag a leak if the verifier itself imported it. Check
        # by walking the module's __dict__ to see if any of the
        # forbidden names are referenced.
        verifier_refs = {
            v.__name__ if hasattr(v, "__name__") else str(v)
            for v in _v.__dict__.values()
            if hasattr(v, "__name__")
        }
        verifier_leaked = leaked & verifier_refs
        assert not verifier_leaked, (
            f"verifier module imports forbidden network lib(s): {verifier_leaked}. "
            f"This breaks AEVF spec §18.4."
        )
