"""Phase 50 source-contract tests — tenant posture score trend."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_posture_score_trend ───────────────────────────────────

def test_aggregator_has_posture_score_trend():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_posture_score_trend" in src


def test_aggregator_posture_score_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_posture_score_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_posture_score_groups_by_day():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2000]
    assert "date_trunc" in snippet or "day" in snippet


def test_aggregator_posture_score_has_allow_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2000]
    assert "allow" in snippet


def test_aggregator_posture_score_has_deny_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2000]
    assert "deny" in snippet


def test_aggregator_posture_score_computes_score():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2500]
    assert "posture_score" in snippet


def test_aggregator_posture_score_zero_fills():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2500]
    assert "range(days)" in snippet or "timedelta" in snippet


def test_aggregator_posture_score_returns_series_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2500]
    assert '"series"' in snippet or "'series'" in snippet


def test_aggregator_posture_score_has_avg_score():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2500]
    assert "avg_score" in snippet


def test_aggregator_posture_score_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_posture_score_trend")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/posture-score-trend ─────────────────────────────────

def test_router_has_posture_score_trend_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "posture-score-trend" in src


def test_router_posture_score_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_posture_score_trend" in src


def test_router_posture_score_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("posture-score-trend")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_posture_score_trend_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "posture-score-trend" in src


def test_gateway_posture_score_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("posture-score-trend")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/posture-score-trend" in snippet


# ── api.js: getPostureScoreTrend ─────────────────────────────────────────────

def test_api_js_has_get_posture_score_trend():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getPostureScoreTrend" in src


def test_api_js_posture_score_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getPostureScoreTrend")
    snippet = src[idx:idx + 200]
    assert "posture-score-trend" in snippet


def test_api_js_posture_score_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getPostureScoreTrend")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── SecurityDashboard.jsx: PostureScoreTrendChart ────────────────────────────

def test_security_dashboard_has_posture_score_chart():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "PostureScoreTrendChart" in src


def test_security_dashboard_uses_get_posture_score_trend():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "getPostureScoreTrend" in src


def test_security_dashboard_has_posture_score_state():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "postureScoreTrend" in src


def test_security_dashboard_posture_chart_shows_score():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("PostureScoreTrendChart")
    snippet = src[idx:idx + 1500]
    assert "posture_score" in snippet


def test_security_dashboard_posture_chart_uses_line_chart():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("PostureScoreTrendChart")
    snippet = src[idx:idx + 1500]
    assert "LineChart" in snippet


def test_security_dashboard_posture_chart_has_reference_line():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("PostureScoreTrendChart")
    snippet = src[idx:idx + 1500]
    assert "ReferenceLine" in snippet


def test_security_dashboard_posture_shows_avg_score():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "avg_score" in src


def test_security_dashboard_has_posture_score_heading():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "Posture" in src
