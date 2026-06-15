"""
Sprint 4 — Incident Storyline Engine.

Submodules:

    storyline.py   — pure-Python reconstruction. Takes a list of Step records,
                     returns a Storyline dataclass with MITRE chain, deduped
                     technique sequence, status, blocked_at_step, title,
                     narrative. Zero I/O. Unit-testable in isolation.

    recorder.py    — Redis-backed writer. Hooks into the gateway middleware
                     after every deny / escalate / quarantine to append a
                     Step to the incident, or open a new one. Idempotent.

    store.py       — Redis read API. Fetches a Storyline by incident_id, lists
                     open incidents for a tenant, decodes JSON-back-to-typed.

Sprint 5 will compute and stamp blast_radius on each Storyline.
Sprint 6 will wire the Storyline into auto-remediation playbooks.
"""
from . import recorder, storyline, store

__all__ = ["recorder", "storyline", "store"]
