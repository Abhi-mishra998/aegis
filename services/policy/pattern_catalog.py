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


# Phase-2 cleanup 2026-06-15 — broader "suspect egress" set.
# `EXFIL_HOSTS` above is the narrow list of *known* exfil destinations the
# attack-chain rules trigger on. Some detectors need a SUPERSET that also
# captures third-party egress points that aren't clearly exfil-only but
# show up in cross-arg PII compositions (tar+curl→external-vendor.com,
# webhook fan-outs through requestbin, etc.). Modeling them as separate
# tuples keeps each rule's intent surface clear — `is_known_exfil_dest`
# stays narrow, `external-egress + PII marker` stays broad.
#
# EXTERNAL_EGRESS_HOSTS includes every EXFIL_HOSTS entry plus the
# "suspect-but-not-clearly-exfil" supplements; consumers should NOT
# union the two manually.
_EXTRA_EGRESS_ONLY: tuple[str, ...] = (
    "external-monitoring.io",
    "external-vendor.com",
    "requestbin.com",
)

EXTERNAL_EGRESS_HOSTS: tuple[str, ...] = tuple(
    sorted(set(EXFIL_HOSTS) | set(_EXTRA_EGRESS_ONLY))
)


# Personal-email domains. The session-intelligence engine uses these to
# spot "sendmail-to-personal-account" exfil patterns where the recipient
# is a free webmail provider rather than a corporate domain. Pure-data,
# not policy — operators can override per-tenant via threat-intel
# (Sprint 7) once Sprint 7.5 wires runtime overlays in.
PERSONAL_EMAIL_DOMAINS: tuple[str, ...] = (
    "@gmail.com",
    "@hotmail.com",
    "@icloud.com",
    "@outlook.com",
    "@proton.me",
    "@protonmail.com",
    "@yahoo.com",
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
_check_sorted_unique("EXTERNAL_EGRESS_HOSTS", EXTERNAL_EGRESS_HOSTS)
_check_sorted_unique("PERSONAL_EMAIL_DOMAINS", PERSONAL_EMAIL_DOMAINS)
# EXTERNAL_EGRESS_HOSTS must be a strict superset of EXFIL_HOSTS — if
# this guard ever fires, someone removed an exfil host from the catalog
# without also pulling it from the egress derivation.
assert set(EXFIL_HOSTS).issubset(set(EXTERNAL_EGRESS_HOSTS)), (
    "EXTERNAL_EGRESS_HOSTS must contain every EXFIL_HOSTS entry"
)
