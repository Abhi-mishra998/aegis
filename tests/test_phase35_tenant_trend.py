"""Phase 35 source-contract tests — tenant 30-day security trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_anomaly_trends ────────────────────────────────────────

def test_aggregator_has_anomaly_trends():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_anomaly_trends" in src


def test_aggregator_trends_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_trends_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_trends_has_count_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_aggregator_trends_has_threats_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 3000]
    assert "threats" in snippet


def test_aggregator_trends_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 3000]
    assert "avg_risk" in snippet


def test_aggregator_trends_has_date_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 3000]
    assert "date" in snippet


def test_aggregator_trends_zero_fills_series():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_anomaly_trends")
    snippet = src[idx:idx + 3000]
    assert "timedelta" in snippet or "date_bucket" in snippet or "zero" in snippet.lower() or "fill" in snippet.lower()


# ── router.py: GET /logs/trends ───────────────────────────────────────────────

def test_router_has_trends_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "trends" in src


def test_router_trends_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_anomaly_trends" in src


def test_router_trends_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("trends")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_trends_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "trends" in src


def test_gateway_trends_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("audit/trends")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/trends" in snippet


# ── api.js: getAnomalyTrends ─────────────────────────────────────────────────

def test_api_js_has_get_anomaly_trends():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAnomalyTrends" in src


def test_api_js_trends_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAnomalyTrends")
    snippet = src[idx:idx + 200]
    assert "trends" in snippet


def test_api_js_trends_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getAnomalyTrends")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── SecurityDashboard.jsx: TenantTrendChart ───────────────────────────────────

def test_dashboard_has_tenant_trend_chart():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "TenantTrendChart" in src


def test_dashboard_uses_get_anomaly_trends():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "getAnomalyTrends" in src


def test_dashboard_has_trends_state():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "trends" in src


def test_dashboard_imports_audit_service():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "auditService" in src


def test_dashboard_trend_uses_composed_chart():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "ComposedChart" in src


def test_dashboard_trend_uses_area():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "Area" in snippet


def test_dashboard_trend_uses_line():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "Line" in snippet


def test_dashboard_trend_shows_count():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_dashboard_trend_shows_threats():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "threats" in snippet


def test_dashboard_trend_shows_avg_risk():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "avg_risk" in snippet


def test_dashboard_trend_has_section_heading():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "30-Day Security Trend" in src or "Security Trend" in src


def test_dashboard_trend_has_dual_yaxis():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "yAxisId" in snippet


def test_dashboard_trend_uses_cartesian_grid():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("TenantTrendChart")
    snippet = src[idx:idx + 3000]
    assert "CartesianGrid" in snippet
