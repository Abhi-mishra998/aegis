"""Phase 47 source-contract tests — finding type breakdown."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_finding_breakdown ─────────────────────────────────────

def test_aggregator_has_finding_breakdown():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_finding_breakdown" in src


def test_aggregator_finding_breakdown_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_finding_breakdown_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_finding_breakdown_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_finding_breakdown_unnests_jsonb():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 3000]
    assert "jsonb_array_elements_text" in snippet


def test_aggregator_finding_breakdown_has_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_aggregator_finding_breakdown_has_pct():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert "pct" in snippet


def test_aggregator_finding_breakdown_returns_findings_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert '"findings"' in snippet or "'findings'" in snippet


def test_aggregator_finding_breakdown_has_total():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert "total" in snippet


def test_aggregator_finding_breakdown_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_finding_breakdown")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/finding-breakdown ───────────────────────────────────

def test_router_has_finding_breakdown_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "finding-breakdown" in src


def test_router_finding_breakdown_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_finding_breakdown" in src


def test_router_finding_breakdown_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("finding-breakdown")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


def test_router_finding_breakdown_accepts_limit():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("finding-breakdown")
    snippet = src[idx:idx + 400]
    assert "limit" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_finding_breakdown_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "finding-breakdown" in src


def test_gateway_finding_breakdown_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("finding-breakdown")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/finding-breakdown" in snippet


# ── api.js: getFindingBreakdown ──────────────────────────────────────────────

def test_api_js_has_get_finding_breakdown():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getFindingBreakdown" in src


def test_api_js_finding_breakdown_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getFindingBreakdown")
    snippet = src[idx:idx + 200]
    assert "finding-breakdown" in snippet


def test_api_js_finding_breakdown_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getFindingBreakdown")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── PolicyAnalytics.jsx: FindingBreakdownChart ───────────────────────────────

def test_policy_analytics_has_finding_breakdown_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "FindingBreakdownChart" in src


def test_policy_analytics_uses_get_finding_breakdown():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "getFindingBreakdown" in src


def test_policy_analytics_has_finding_breakdown_state():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "findingBreakdown" in src


def test_policy_analytics_chart_shows_finding():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("FindingBreakdownChart")
    snippet = src[idx:idx + 1500]
    assert "finding" in snippet


def test_policy_analytics_chart_shows_count():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("FindingBreakdownChart")
    snippet = src[idx:idx + 1500]
    assert "count" in snippet


def test_policy_analytics_chart_shows_pct():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("FindingBreakdownChart")
    snippet = src[idx:idx + 1500]
    assert "pct" in snippet


def test_policy_analytics_chart_has_bar():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("FindingBreakdownChart")
    snippet = src[idx:idx + 1500]
    assert "width" in snippet or "barPct" in snippet


def test_policy_analytics_has_finding_breakdown_heading():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Finding" in src
