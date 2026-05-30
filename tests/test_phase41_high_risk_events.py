"""Phase 41 source-contract tests — high-risk event feed."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_high_risk_events ──────────────────────────────────────

def test_aggregator_has_high_risk_events():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_high_risk_events" in src


def test_aggregator_high_risk_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_high_risk_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_high_risk_accepts_threshold():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 500]
    assert "threshold" in snippet


def test_aggregator_high_risk_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_high_risk_filters_by_score():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 3000]
    assert "risk_score" in snippet and ("threshold" in snippet or ">=" in snippet)


def test_aggregator_high_risk_orders_by_score():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 3000]
    assert "order_by" in snippet or "ORDER BY" in snippet


def test_aggregator_high_risk_has_agent_id():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 2500]
    assert "agent_id" in snippet


def test_aggregator_high_risk_has_tool():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 2500]
    assert "tool" in snippet


def test_aggregator_high_risk_has_decision():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 2500]
    assert "decision" in snippet


def test_aggregator_high_risk_has_findings():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 2500]
    assert "findings" in snippet


def test_aggregator_high_risk_returns_events_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 2500]
    assert '"events"' in snippet or "'events'" in snippet


def test_aggregator_high_risk_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_high_risk_events")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/high-risk-events ────────────────────────────────────

def test_router_has_high_risk_events_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "high-risk-events" in src


def test_router_high_risk_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_high_risk_events" in src


def test_router_high_risk_accepts_threshold():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("high-risk-events")
    snippet = src[idx:idx + 500]
    assert "threshold" in snippet


def test_router_high_risk_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("high-risk-events")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_high_risk_events_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "high-risk-events" in src


def test_gateway_high_risk_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("high-risk-events")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/high-risk-events" in snippet


# ── api.js: getHighRiskEvents ─────────────────────────────────────────────────

def test_api_js_has_get_high_risk_events():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getHighRiskEvents" in src


def test_api_js_high_risk_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getHighRiskEvents")
    snippet = src[idx:idx + 200]
    assert "high-risk-events" in snippet


def test_api_js_high_risk_accepts_threshold():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getHighRiskEvents")
    snippet = src[idx:idx + 200]
    assert "threshold" in snippet


# ── RiskEngine.jsx: HighRiskEventFeed ────────────────────────────────────────

def test_risk_engine_has_high_risk_feed():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "HighRiskEventFeed" in src


def test_risk_engine_uses_get_high_risk_events():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "getHighRiskEvents" in src


def test_risk_engine_has_high_risk_state():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "highRiskEvents" in src


def test_risk_engine_feed_shows_risk_score():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("HighRiskEventFeed")
    snippet = src[idx:idx + 3000]
    assert "risk_score" in snippet


def test_risk_engine_feed_shows_decision():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("HighRiskEventFeed")
    snippet = src[idx:idx + 3000]
    assert "decision" in snippet


def test_risk_engine_feed_shows_findings_tags():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("HighRiskEventFeed")
    snippet = src[idx:idx + 2500]
    assert "findings" in snippet


def test_risk_engine_feed_links_to_forensics():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("HighRiskEventFeed")
    snippet = src[idx:idx + 3000]
    assert "forensics" in snippet or "navigate" in snippet


def test_risk_engine_feed_has_section_heading():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "High Risk Event Feed" in src or "High Risk" in src
