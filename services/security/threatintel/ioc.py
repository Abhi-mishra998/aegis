"""Sprint 7 — Indicator-of-Compromise value type.

Pure dataclass + the kind/severity vocabulary. No I/O. Importing this
module is safe in any context, including tests that don't have Redis.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any


# Kinds — strings so they round-trip cleanly through JSON without a
# custom encoder, and so new kinds can land later without invalidating
# stored records.
KIND_EXFIL_HOST       = "exfil_host"
KIND_C2_DOMAIN        = "c2_domain"
KIND_OFFSHORE_TOKEN   = "offshore_token"
KIND_DESTRUCTIVE_SHELL = "destructive_shell"
KIND_MALICIOUS_PATH   = "malicious_path"
KIND_PRIVILEGE_TOKEN  = "privilege_token"

ALL_KINDS = frozenset({
    KIND_EXFIL_HOST,
    KIND_C2_DOMAIN,
    KIND_OFFSHORE_TOKEN,
    KIND_DESTRUCTIVE_SHELL,
    KIND_MALICIOUS_PATH,
    KIND_PRIVILEGE_TOKEN,
})

# Severities mirror the storyline / signal-registry vocabulary so the
# whole platform speaks the same language.
SEV_CRITICAL = "critical"
SEV_HIGH     = "high"
SEV_MEDIUM   = "medium"
SEV_LOW      = "low"

ALL_SEVERITIES = frozenset({SEV_CRITICAL, SEV_HIGH, SEV_MEDIUM, SEV_LOW})

# Conventional sources. Not enforced — operators can supply arbitrary
# strings (the SOC's own ticket-id is a perfectly good source) but the
# defaults give the UI sensible filter options.
SOURCE_HARDCODED   = "aegis_default"     # ships with the platform
SOURCE_OPERATOR    = "operator"          # manually added via the API
SOURCE_FEED        = "feed"              # came from an HttpFeedProvider


@dataclass(frozen=True)
class IOCRecord:
    """One IOC row. The id is derived from (tenant_id, kind, value) so
    the same value re-imported by a feed doesn't accumulate duplicates.

    Match semantics:
      * `KIND_EXFIL_HOST`, `KIND_C2_DOMAIN`, `KIND_MALICIOUS_PATH`,
        `KIND_OFFSHORE_TOKEN`, `KIND_PRIVILEGE_TOKEN` — substring match
        against the candidate, case-insensitive (values are lowercased
        on write).
      * `KIND_DESTRUCTIVE_SHELL` — Python regex compiled from `value`.
        Compilation failures are rejected at write-time so the runtime
        path never hits a broken pattern.
    """
    id:          str
    tenant_id:   str
    kind:        str
    value:       str
    severity:    str
    source:      str
    created_ts:  float
    actor:       str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_id(tenant_id: str, kind: str, value: str) -> str:
    """Deterministic 12-char id derived from (tenant_id, kind, value).

    Stable across processes + restarts; a feed that re-uploads the same
    record produces the same id, so the upsert path is idempotent.
    """
    digest = hashlib.sha256(f"{tenant_id}|{kind}|{value}".encode("utf-8")).hexdigest()
    return digest[:12]
