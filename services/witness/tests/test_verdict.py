"""Verdict engine unit tests. No fixtures, no external deps."""
from __future__ import annotations

from services.witness.schemas import Observation, VerdictRequest
from services.witness.verdict import evaluate


def _obs(gid: str, t: str, detail: str = "x", extra: dict | None = None) -> Observation:
    return Observation(
        gate_decision_id=gid, type=t, detail=detail,  # type: ignore[arg-type]
        ts="2026-07-21T14:02:11Z",
        extra=extra or {},
    )


def test_corroborated_c2_with_net_and_api() -> None:
    req = VerdictRequest(
        gate_decision_id="gd_1", claim="delete crm.record 123",
        action_class="C2", expected_evidence=["net", "api"],
    )
    obs = [
        _obs("gd_1", "net", "TLS crm.internal:443"),
        _obs("gd_1", "api", "DELETE /records/123 → 200", extra={"status_code": 200}),
    ]
    assert evaluate(req, obs) == "CORROBORATED"


def test_unobserved_when_no_observations() -> None:
    req = VerdictRequest(gate_decision_id="gd_2", claim="anything", action_class="C2")
    assert evaluate(req, []) == "UNOBSERVED"


def test_unobserved_when_witness_degraded() -> None:
    req = VerdictRequest(gate_decision_id="gd_3", claim="anything", action_class="C2")
    obs = [_obs("gd_3", "net", "x"), _obs("gd_3", "api", "x", extra={"status_code": 200})]
    assert evaluate(req, obs, witness_degraded=True) == "UNOBSERVED"


def test_contradicted_when_api_status_non_2xx_and_claim_expected_success() -> None:
    req = VerdictRequest(
        gate_decision_id="gd_4", claim="successful payment",
        action_class="C3", expected_evidence=["net", "api"],
    )
    obs = [
        _obs("gd_4", "net", "TLS payments:443"),
        _obs("gd_4", "api", "POST /pay → 500", extra={"status_code": 500}),
    ]
    assert evaluate(req, obs) == "CONTRADICTED"


def test_non_2xx_not_contradicted_when_claim_is_error() -> None:
    req = VerdictRequest(
        gate_decision_id="gd_5", claim="expected rejection: bad input",
        action_class="C2", expected_evidence=["api"],
    )
    obs = [_obs("gd_5", "api", "POST /x → 400", extra={"status_code": 400})]
    assert evaluate(req, obs) == "CORROBORATED"


def test_c1_corroborates_on_any_evidence_even_if_expected_missing() -> None:
    req = VerdictRequest(
        gate_decision_id="gd_6", claim="internal message send",
        action_class="C1", expected_evidence=["net", "api"],
    )
    obs = [_obs("gd_6", "api", "POST /msg → 200", extra={"status_code": 200})]
    assert evaluate(req, obs) == "CORROBORATED"


def test_c2_missing_expected_evidence_is_unobserved_not_corroborated() -> None:
    req = VerdictRequest(
        gate_decision_id="gd_7", claim="delete",
        action_class="C2", expected_evidence=["net", "api", "fs"],
    )
    obs = [_obs("gd_7", "net", "x")]  # no api, no fs
    assert evaluate(req, obs) == "UNOBSERVED"
