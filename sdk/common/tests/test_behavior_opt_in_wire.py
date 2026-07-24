"""Verify the behavior_opt_in gate is consulted at the learned-model
consumption site in services/behavior/service.py. Test doesn't run the
full behavior service; it inspects the source file to prove the wire
exists AND unit-tests the gate itself for the invariants that matter."""
from __future__ import annotations

from pathlib import Path

from sdk.common.behavior_opt_in import gate_score_consumption, get_mode_for


class TestBehaviorOptInWire:
    def test_service_calls_gate_before_intelligence_engine(self):
        """Static check: `gate_score_consumption` is invoked in the same
        code block that gates `intelligence_engine.report_anomaly`.
        A future refactor that removes the gate would fail this."""
        src = (Path(__file__).parent.parent.parent.parent
               / "services" / "behavior" / "service.py").read_text()
        assert "gate_score_consumption" in src
        # And the gate must be BEFORE the learned call — order matters.
        gate_idx = src.index("gate_score_consumption")
        report_idx = src.index("intelligence_engine.report_anomaly")
        assert gate_idx < report_idx


class TestGateInvariants:
    def test_gate_input_is_ALWAYS_refused_regardless_of_env(self, monkeypatch):
        # Even for an enabled tenant + advisory mode, gate_input is refused.
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_TENANTS", "acme")
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_MODE", "advisory")
        assert not gate_score_consumption("acme", "gate_input")

    def test_gate_input_refused_even_if_env_says_authoritative(self, monkeypatch):
        # Someone injects a bogus mode via env → still refused, no gate_input.
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_TENANTS", "acme")
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_MODE", "authoritative")
        assert not gate_score_consumption("acme", "gate_input")
        # Invalid mode also falls back to off → display refused for good measure.
        assert not gate_score_consumption("acme", "display")

    def test_display_allowed_when_tenant_opted_in_and_mode_advisory(self, monkeypatch):
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_TENANTS", "acme")
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_MODE", "advisory")
        assert gate_score_consumption("acme", "display")

    def test_display_refused_when_tenant_not_opted_in(self, monkeypatch):
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_TENANTS", "beta")
        monkeypatch.setenv("ACP_BEHAVIOR_FINGERPRINTING_MODE", "advisory")
        assert not gate_score_consumption("acme", "display")

    def test_no_env_default_off(self, monkeypatch):
        monkeypatch.delenv("ACP_BEHAVIOR_FINGERPRINTING_TENANTS", raising=False)
        monkeypatch.delenv("ACP_BEHAVIOR_FINGERPRINTING_MODE", raising=False)
        assert get_mode_for("anyone") == "off"
        assert not gate_score_consumption("anyone", "display")
