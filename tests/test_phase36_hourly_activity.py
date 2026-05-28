"""Phase 36 source-contract tests — decision velocity by hour-of-day."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_hourly_activity ───────────────────────────────────────

def test_aggregator_has_hourly_activity():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_hourly_activity" in src


def test_aggregator_hourly_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_hourly_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_hourly_extracts_hour():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "hour" in snippet and ("extract" in snippet.lower() or "HOUR" in snippet)


def test_aggregator_hourly_has_count_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_aggregator_hourly_has_deny_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "deny_count" in snippet


def test_aggregator_hourly_has_avg_risk():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "avg_risk" in snippet


def test_aggregator_hourly_returns_24_buckets():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "24" in snippet or "range(24)" in snippet


def test_aggregator_hourly_returns_buckets_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert '"buckets"' in snippet or "'buckets'" in snippet


def test_aggregator_hourly_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_hourly_activity")
    snippet = src[idx:idx + 3000]
    assert "computed_at" in snippet


# ── router.py: GET /logs/hourly-activity ─────────────────────────────────────

def test_router_has_hourly_activity_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "hourly-activity" in src


def test_router_hourly_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_hourly_activity" in src


def test_router_hourly_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("hourly-activity")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_hourly_activity_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "hourly-activity" in src


def test_gateway_hourly_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("hourly-activity")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/hourly-activity" in snippet


# ── api.js: getHourlyActivity ────────────────────────────────────────────────

def test_api_js_has_get_hourly_activity():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getHourlyActivity" in src


def test_api_js_hourly_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getHourlyActivity")
    snippet = src[idx:idx + 200]
    assert "hourly-activity" in snippet


def test_api_js_hourly_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getHourlyActivity")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── PolicyAnalytics.jsx: HourlyActivityChart ─────────────────────────────────

def test_policy_analytics_has_hourly_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "HourlyActivityChart" in src


def test_policy_analytics_uses_get_hourly_activity():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "getHourlyActivity" in src


def test_policy_analytics_has_hourly_state():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "hourlyActivity" in src


def test_policy_analytics_imports_composed_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "ComposedChart" in src


def test_policy_analytics_chart_uses_bar():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "Bar" in snippet


def test_policy_analytics_chart_uses_line():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "Line" in snippet


def test_policy_analytics_chart_shows_count():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_policy_analytics_chart_shows_deny_count():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "deny_count" in snippet


def test_policy_analytics_chart_shows_avg_risk():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "avg_risk" in snippet


def test_policy_analytics_chart_has_dual_axis():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "yAxisId" in snippet


def test_policy_analytics_has_section_heading():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Decision Velocity" in src or "Hourly" in src


def test_policy_analytics_chart_has_cartesian_grid():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("HourlyActivityChart")
    snippet = src[idx:idx + 3000]
    assert "CartesianGrid" in snippet
