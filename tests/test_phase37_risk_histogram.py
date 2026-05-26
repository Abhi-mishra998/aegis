"""Phase 37 source-contract tests — risk score distribution histogram."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_risk_histogram ────────────────────────────────────────

def test_aggregator_has_risk_histogram():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_risk_histogram" in src


def test_aggregator_histogram_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_histogram_accepts_bins():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 500]
    assert "bins" in snippet


def test_aggregator_histogram_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_histogram_uses_floor_bucketing():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "FLOOR" in snippet or "floor" in snippet.lower()


def test_aggregator_histogram_returns_buckets_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert '"buckets"' in snippet or "'buckets'" in snippet


def test_aggregator_histogram_has_bin_label():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert '"bin"' in snippet or "'bin'" in snippet


def test_aggregator_histogram_has_low_high():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "low" in snippet and "high" in snippet


def test_aggregator_histogram_has_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "count" in snippet


def test_aggregator_histogram_has_total():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "total" in snippet


def test_aggregator_histogram_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "computed_at" in snippet


def test_aggregator_histogram_zero_fills_missing_bins():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_risk_histogram")
    snippet = src[idx:idx + 2000]
    assert "range(" in snippet


# ── router.py: GET /logs/risk-histogram ──────────────────────────────────────

def test_router_has_risk_histogram_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "risk-histogram" in src


def test_router_histogram_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_risk_histogram" in src


def test_router_histogram_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("risk-histogram")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_router_histogram_accepts_bins():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("risk-histogram")
    snippet = src[idx:idx + 500]
    assert "bins" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_risk_histogram_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "risk-histogram" in src


def test_gateway_histogram_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("risk-histogram")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/risk-histogram" in snippet


# ── api.js: getRiskHistogram ─────────────────────────────────────────────────

def test_api_js_has_get_risk_histogram():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getRiskHistogram" in src


def test_api_js_histogram_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getRiskHistogram")
    snippet = src[idx:idx + 200]
    assert "risk-histogram" in snippet


def test_api_js_histogram_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getRiskHistogram")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── RiskEngine.jsx: RiskHistogram ────────────────────────────────────────────

def test_risk_engine_has_risk_histogram_component():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "RiskHistogram" in src


def test_risk_engine_uses_get_risk_histogram():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "getRiskHistogram" in src


def test_risk_engine_has_histogram_state():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "riskHistogram" in src


def test_risk_engine_histogram_imports_bar_chart():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "BarChart" in src


def test_risk_engine_histogram_imports_cell():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "Cell" in src


def test_risk_engine_histogram_color_codes_by_risk():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("RiskHistogram")
    snippet = src[idx:idx + 1000]
    assert "#ef4444" in snippet or "red" in snippet.lower()


def test_risk_engine_histogram_shows_count():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("RiskHistogram")
    snippet = src[idx:idx + 1500]
    assert "count" in snippet


def test_risk_engine_histogram_has_section_heading():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "Risk Score Distribution" in src or "Distribution" in src


def test_risk_engine_histogram_shows_total():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "riskHistogram?.total" in src or "riskHistogram?.buckets" in src
