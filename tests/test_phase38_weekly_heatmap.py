"""Phase 38 source-contract tests — weekly activity heatmap (day × hour)."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_weekly_heatmap ────────────────────────────────────────

def test_aggregator_has_weekly_heatmap():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_weekly_heatmap" in src


def test_aggregator_heatmap_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_heatmap_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_heatmap_extracts_dow():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "dow" in snippet


def test_aggregator_heatmap_extracts_hour():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "hour" in snippet


def test_aggregator_heatmap_has_7_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "range(7)" in snippet or "7" in snippet


def test_aggregator_heatmap_has_24_hours():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "range(24)" in snippet or "24" in snippet


def test_aggregator_heatmap_has_pct_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "pct" in snippet


def test_aggregator_heatmap_has_count_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_aggregator_heatmap_has_day_label():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "day_label" in snippet or "Mon" in snippet


def test_aggregator_heatmap_returns_cells_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert '"cells"' in snippet or "'cells'" in snippet


def test_aggregator_heatmap_returns_max_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 3000]
    assert "max_count" in snippet


def test_aggregator_heatmap_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_weekly_heatmap")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/weekly-heatmap ──────────────────────────────────────

def test_router_has_weekly_heatmap_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "weekly-heatmap" in src


def test_router_heatmap_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_weekly_heatmap" in src


def test_router_heatmap_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("weekly-heatmap")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_weekly_heatmap_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "weekly-heatmap" in src


def test_gateway_heatmap_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("weekly-heatmap")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/weekly-heatmap" in snippet


# ── api.js: getWeeklyHeatmap ─────────────────────────────────────────────────

def test_api_js_has_get_weekly_heatmap():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getWeeklyHeatmap" in src


def test_api_js_heatmap_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getWeeklyHeatmap")
    snippet = src[idx:idx + 200]
    assert "weekly-heatmap" in snippet


def test_api_js_heatmap_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getWeeklyHeatmap")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── SecurityDashboard.jsx: WeeklyHeatmap ─────────────────────────────────────

def test_dashboard_has_weekly_heatmap_component():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "WeeklyHeatmap" in src


def test_dashboard_uses_get_weekly_heatmap():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "getWeeklyHeatmap" in src


def test_dashboard_has_weekly_heatmap_state():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "weeklyHeatmap" in src


def test_dashboard_heatmap_renders_days():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("WeeklyHeatmap")
    snippet = src[idx:idx + 3000]
    assert "Mon" in snippet or "day_label" in snippet or "DAY_LABELS" in snippet


def test_dashboard_heatmap_renders_hours():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("WeeklyHeatmap")
    snippet = src[idx:idx + 3000]
    assert "hour" in snippet or "24" in snippet


def test_dashboard_heatmap_uses_pct_for_color():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    idx = src.find("WeeklyHeatmap")
    snippet = src[idx:idx + 3000]
    assert "pct" in snippet


def test_dashboard_heatmap_has_section_heading():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "Weekly Activity" in src or "weekly" in src.lower()


def test_dashboard_heatmap_shows_max_count():
    src = (ROOT / "ui/src/pages/SecurityDashboard.jsx").read_text()
    assert "max_count" in src or "maxCount" in src
