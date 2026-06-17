"""Aegis Security namespace — Sprint 1+ refactor target.

Each submodule here owns ONE security concern and is the single source of
truth for that concern across the codebase:

    signal_registry.py     — every signal Aegis emits (Sprint 1)
    objectives/            — per-MITRE-tactic detectors (Sprint 3)
    incidents/             — storyline engine (Sprint 4)
    iag/                   — identity & access graph (Sprint 5)
    remediation/           — auto-response handlers (Sprint 6)
    threatintel/           — pluggable feed providers (Sprint 7)

If you find logic that should be in one of these but isn't, the refactor
is incomplete — file a follow-up, don't duplicate it in the legacy spot.

Security — library module, NOT an HTTP service.

This package is imported by other services. It does not run an HTTP
server, has no Dockerfile, and is not started by docker-compose. Do
not add `main.py` here.
"""
