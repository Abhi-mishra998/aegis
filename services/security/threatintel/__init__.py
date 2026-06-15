"""Sprint 7 — Threat-Intel Provider Layer.

Submodules:
  ioc.py        — pure IOCRecord + KIND_* constants
  store.py      — Redis read/write for the per-tenant IOC cache
  providers.py  — BaseProvider + StaticListProvider + HttpFeedProvider
                  + orchestrator that runs every configured feed
  runtime.py    — match / match_any / matches_for_kind — the helpers
                  the canonical evaluator calls on the request path

Hardcoded constants in canonical.py stay as the floor — the runtime
augments them with operator-added IOCs but never replaces them, so
nothing regresses if the cache is empty or Redis blips.
"""
from . import ioc, providers, runtime, store  # noqa: F401

__all__ = ["ioc", "providers", "runtime", "store"]
