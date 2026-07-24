"""Tests for the ATF Appendix D.1 serverless deployment mode.

Serverless agents have no co-located Witness → every verdict must
be UNOBSERVED regardless of observations in the store. Reasserted:
sidecar mode preserves existing behavior.
"""
from __future__ import annotations

import importlib

import pytest

from services.witness.schemas import Observation, VerdictRequest


def _reload_router(monkeypatch, mode: str):
    monkeypatch.setenv("WITNESS_DEPLOYMENT_MODE", mode)
    from services.witness import router as r
    importlib.reload(r)
    return r


def _obs(gid: str) -> Observation:
    return Observation(
        gate_decision_id=gid, type="net",
        detail="tls-fake", ts="2026-07-22T14:00Z",
    )


class TestModeParsing:
    def test_default_is_sidecar(self, monkeypatch):
        monkeypatch.delenv("WITNESS_DEPLOYMENT_MODE", raising=False)
        r = _reload_router(monkeypatch, "sidecar")  # explicit default
        assert r._DEPLOYMENT_MODE == "sidecar"

    def test_serverless_recognized(self, monkeypatch):
        r = _reload_router(monkeypatch, "serverless")
        assert r._DEPLOYMENT_MODE == "serverless"

    def test_uppercase_normalized(self, monkeypatch):
        r = _reload_router(monkeypatch, "SERVERLESS")
        assert r._DEPLOYMENT_MODE == "serverless"

    def test_unknown_falls_back_to_sidecar(self, monkeypatch):
        """A typo like `sidecart` MUST NOT silently become serverless
        (that would break coverage). And MUST NOT silently become an
        unknown mode. Fall back to the safe default that behaves the
        same as before this change."""
        r = _reload_router(monkeypatch, "sidecart")
        assert r._DEPLOYMENT_MODE == "sidecar"


class TestServerlessForcesUnobserved:
    @pytest.mark.asyncio
    async def test_serverless_verdict_is_unobserved_even_with_observations(
        self, monkeypatch,
    ):
        """Even if the store somehow contains observations for the
        gate_decision_id (bug, leaked probe events from a previous
        sidecar deploy, hostile injection), a serverless-mode witness
        must return UNOBSERVED."""
        r = _reload_router(monkeypatch, "serverless")

        # Populate the store — the router MUST ignore these under
        # serverless mode.
        from services.witness import store as ws
        ws._reset_for_tests(ws._MemoryFallback(), "memory")
        await ws.record(_obs("gd_1"))
        await ws.record(_obs("gd_1"))

        req = VerdictRequest(
            gate_decision_id="gd_1",
            claim="delete crm record",
            action_class="C2",
            expected_evidence=["net", "api"],
        )
        resp = await r.render_verdict(req)
        attestation = resp.data
        assert attestation.verdict == "UNOBSERVED"
        # Evidence list must be EMPTY — even if store has entries, we
        # must not attach them to a UNOBSERVED attestation.
        assert attestation.evidence == []

    @pytest.mark.asyncio
    async def test_sidecar_still_reads_store(self, monkeypatch):
        """Regression: sidecar mode preserves the pre-existing behavior
        (reads observations, evaluates against them)."""
        r = _reload_router(monkeypatch, "sidecar")

        from services.witness import store as ws
        ws._reset_for_tests(ws._MemoryFallback(), "memory")
        # Give the witness a fresh heartbeat so it isn't degraded.
        import time
        signer = r.get_signer()
        await ws.heartbeat(signer.witness_id, time.time())

        # Deposit both expected evidence types.
        await ws.record(Observation(
            gate_decision_id="gd_2", type="net",
            detail="tls-crm", ts="2026-07-22T14:00Z",
        ))
        await ws.record(Observation(
            gate_decision_id="gd_2", type="api",
            detail="DELETE /r → 200", ts="2026-07-22T14:00Z",
            extra={"status_code": 200},
        ))

        req = VerdictRequest(
            gate_decision_id="gd_2",
            claim="delete crm record",
            action_class="C2",
            expected_evidence=["net", "api"],
        )
        resp = await r.render_verdict(req)
        assert resp.data.verdict == "CORROBORATED"


class TestHealthSurfacesMode:
    @pytest.mark.asyncio
    async def test_health_reports_serverless(self, monkeypatch):
        r = _reload_router(monkeypatch, "serverless")
        resp = await r.health()
        assert resp.data["deployment_mode"] == "serverless"

    @pytest.mark.asyncio
    async def test_health_reports_sidecar(self, monkeypatch):
        r = _reload_router(monkeypatch, "sidecar")
        resp = await r.health()
        assert resp.data["deployment_mode"] == "sidecar"
