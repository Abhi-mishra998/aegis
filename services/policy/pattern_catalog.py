"""Sprint 8 — shared pattern catalog.

Single source of truth for the pattern lists the fast path
(`canonical.py`, `local_action_semantics.py`) and the slow path
(`policies/action_semantics_deny.rego`, via `rego_emitter.py`) both
consume. Before Sprint 8 these lists lived in both files; every new
pattern landed in one, got forgotten in the other, and a slow-path
caller (no JWT claims) ran an older policy than the fast-path caller.

The threat-intel runtime layer (Sprint 7) overlays additional values at
request time. The catalog is the FLOOR — what ships in the Aegis
binary even when Redis is empty.

Conventions:
  * Tuples, not lists or sets — frozen at import; eliminates the
    possibility of test code mutating shared state across cases.
  * Sorted alphabetically — makes the generated Rego deterministic and
    the diff legible. The drift test will fail if a contributor adds
    a value out of order.
  * Lowercase — every consumer normalizes candidate strings to lower
    before comparing.
"""
from __future__ import annotations


# Hosts/paths known to be used for data exfiltration in real incidents.
# Substring match — if `transfer.sh` appears anywhere in the candidate
# URL the rule fires. Keep in sync with the SOC's curated default IOC
# set surfaced by `services/security/threatintel/providers.global_defaults_providers`.
EXFIL_HOSTS: tuple[str, ...] = (
    "0x0.st",
    "anonfiles.com",
    "discord.com/api/webhooks",
    "filebin.net",
    "gist.github.com",
    "ngrok.io",
    "pastebin.com",
    "transfer.sh",
    "trycloudflare.com",
    "webhook.site",
)


# Money-laundering / offshore-banking trigger tokens. Substring match
# against the request blob lowercased.
OFFSHORE_TOKENS: tuple[str, ...] = (
    "beneficiary-offshore",
    "british_virgin_islands",
    "bvi",
    "cayman",
    "offshore",
    "panama_papers",
)


def _check_sorted_unique(name: str, items: tuple[str, ...]) -> None:
    """Defensive guard run at import time so a typo in the catalog
    raises immediately, not at the drift test."""
    if list(items) != sorted(set(items)):
        raise AssertionError(
            f"pattern_catalog.{name}: not sorted-unique; "
            f"got {items!r}"
        )


_check_sorted_unique("EXFIL_HOSTS", EXFIL_HOSTS)
_check_sorted_unique("OFFSHORE_TOKENS", OFFSHORE_TOKENS)
