"""Phase 45 source-contract tests — daily risk score percentile trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_risk_percentile_trend ─────────────────────────────────

def test_aggregator_has_risk_percentile_trend():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_risk_percentile_trend" in src


def test_aggregator_percentile_trend_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_percentile_trend_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_percentile_trend_uses_percentile_cont():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 3000]
    assert "percentile_cont" in snippet


def test_aggregator_percentile_trend_has_p50():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 3000]
    assert "p50" in snippet


def test_aggregator_percentile_trend_has_p75():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 3000]
    assert "p75" in snippet


def test_aggregator_percentile_trend_has_p95():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 3000]
    assert "p95" in snippet


def test_aggregator_percentile_trend_zero_fills():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 2500]
    assert "range(days)" in snippet or "timedelta" in snippet


def test_aggregator_percentile_trend_returns_series_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 2500]
    assert '"series"' in snippet or "'series'" in snippet


def test_aggregator_percentile_trend_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


def test_aggregator_percentile_trend_has_date_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_percentile_trend")
    snippet = src[idx:idx + 2500]
    assert '"date"' in snippet or "'date'" in snippet


# ── router.py: GET /logs/risk-percentile-trend ───────────────────────────────

def test_router_has_risk_percentile_trend_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "risk-percentile-trend" in src


def test_router_risk_percentile_trend_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_risk_percentile_trend" in src


def test_router_risk_percentile_trend_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("risk-percentile-trend")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_risk_percentile_trend_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "risk-percentile-trend" in src


def test_gateway_risk_percentile_trend_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("risk-percentile-trend")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/risk-percentile-trend" in snippet


# ── api.js: getRiskPercentileTrend ───────────────────────────────────────────

def test_api_js_has_get_risk_percentile_trend():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getRiskPercentileTrend" in src


def test_api_js_risk_percentile_trend_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getRiskPercentileTrend")
    snippet = src[idx:idx + 200]
    assert "risk-percentile-trend" in snippet


def test_api_js_risk_percentile_trend_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getRiskPercentileTrend")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── RiskEngine.jsx: RiskPercentileTrendChart ─────────────────────────────────

def test_risk_engine_has_percentile_trend_chart():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "RiskPercentileTrendChart" in src


def test_risk_engine_uses_get_risk_percentile_trend():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "getRiskPercentileTrend" in src


def test_risk_engine_has_percentile_trend_state():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "riskPercentileTrend" in src


def test_risk_engine_percentile_chart_has_p50_line():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("RiskPercentileTrendChart")
    snippet = src[idx:idx + 1500]
    assert "p50" in snippet


def test_risk_engine_percentile_chart_has_p75_line():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("RiskPercentileTrendChart")
    snippet = src[idx:idx + 1500]
    assert "p75" in snippet


def test_risk_engine_percentile_chart_has_p95_line():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("RiskPercentileTrendChart")
    snippet = src[idx:idx + 1500]
    assert "p95" in snippet


def test_risk_engine_percentile_chart_uses_line_chart():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("RiskPercentileTrendChart")
    snippet = src[idx:idx + 1500]
    assert "LineChart" in snippet


def test_risk_engine_has_percentile_trend_heading():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "Percentile" in src or "percentile" in src
