"""Sprint 5 — Identity & Access Graph + Blast Radius.

Three submodules:

  graph.py     — pure compute_blast_radius() + dataclasses; deterministic,
                 no I/O, easy to unit-test against synthetic graphs.
  store.py     — Redis read/write for the IAG cache; mirror of Sprint 4's
                 incident store conventions.
  ingestion.py — pluggable adapters that pull authoritative role +
                 permission + resource edges into the Redis cache.

The router lives at services/gateway/routers/iag.py.
"""
from . import graph, store, ingestion  # noqa: F401

__all__ = ["graph", "store", "ingestion"]
