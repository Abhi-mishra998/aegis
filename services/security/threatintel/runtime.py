"""Sprint 7 — Runtime IOC lookup helpers.

What the canonical evaluator + objective modules call on the request
path. Substring-match kinds (exfil_host, c2_domain, malicious_path,
privilege_token, offshore_token) consult a single SMEMBERS over the
per-tenant + global overlay sets and the candidate is lowercased.
Regex kinds (destructive_shell) are matched against the value set with
re.search.

Fail-open: any Redis error returns False so detection falls back to the
hardcoded constants the canonical eval keeps. We never want
threat-intel infrastructure to make detection MORE permissive than
today — but we also can't take a tenant down because Redis blipped.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from . import store
from .ioc import KIND_DESTRUCTIVE_SHELL


# In-process regex cache so we don't recompile on every request. Bounded
# at 256 patterns per kind — that's far more than any tenant will
# realistically configure, and a defensive cap so a buggy provider
# can't OOM the gateway.
_REGEX_CACHE: dict[tuple[str, str], re.Pattern[str]] = {}
_REGEX_CACHE_MAX = 256


def _compile_or_none(pat: str) -> re.Pattern[str] | None:
    key = ("re", pat)
    cached = _REGEX_CACHE.get(key)
    if cached is not None:
        return cached
    if len(_REGEX_CACHE) >= _REGEX_CACHE_MAX:
        # Simple FIFO eviction by re-creating the dict on overflow —
        # cheap (we max at 256) and avoids importing OrderedDict.
        _REGEX_CACHE.clear()
    try:
        compiled = re.compile(pat, flags=re.IGNORECASE)
    except re.error:
        # Bad pattern — drop it; runtime should never raise.
        return None
    _REGEX_CACHE[key] = compiled
    return compiled


async def matches_for_kind(
    redis: Any, *, tenant_id: str, kind: str,
) -> set[str]:
    """All stored values for one kind, unioned across the tenant + global.

    Used by the canonical eval when it wants the full list (e.g. to log
    "we considered these N IOCs"). Returns an empty set on Redis fault
    so callers can branch on emptiness without try/except churn.
    """
    try:
        tenant_vals = await store.values_for_kind(
            redis, tenant_id=tenant_id, kind=kind,
        )
        global_vals = await store.values_for_kind(
            redis, tenant_id=store.GLOBAL_TENANT_ID, kind=kind,
        )
        return tenant_vals | global_vals
    except Exception:
        return set()


async def match(
    redis: Any, *, tenant_id: str, kind: str, candidate: str,
) -> bool:
    """Does any IOC in (tenant, global) match `candidate` for `kind`?

    Substring semantics for everything except `destructive_shell` (regex).
    """
    if not candidate:
        return False
    vals = await matches_for_kind(redis, tenant_id=tenant_id, kind=kind)
    if not vals:
        return False
    if kind == KIND_DESTRUCTIVE_SHELL:
        for pat in vals:
            compiled = _compile_or_none(pat)
            if compiled is not None and compiled.search(candidate):
                return True
        return False
    needle = candidate.lower()
    for v in vals:
        if v and v in needle:
            return True
    return False


async def match_any(
    redis: Any, *, tenant_id: str, kind: str, candidates: Iterable[str],
) -> bool:
    """OR over multiple candidates.

    Cheaper than calling `match()` N times because the value set is
    fetched once and reused. Used by the canonical eval which often has
    both a `host` and a `url` to consider.
    """
    cands = [c for c in candidates if c]
    if not cands:
        return False
    vals = await matches_for_kind(redis, tenant_id=tenant_id, kind=kind)
    if not vals:
        return False
    if kind == KIND_DESTRUCTIVE_SHELL:
        compiled_list = [p for p in (_compile_or_none(v) for v in vals) if p is not None]
        return any(p.search(c) for p in compiled_list for c in cands)
    needles = [c.lower() for c in cands]
    return any(v and any(v in n for n in needles) for v in vals)
