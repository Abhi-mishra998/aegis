"""Phase 42 source-contract tests — top deny reason frequency analysis."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_deny_reasons ──────────────────────────────────────────

def test_aggregator_has_deny_reasons():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_deny_reasons" in src


def test_aggregator_deny_reasons_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_deny_reasons_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_deny_reasons_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2000]
    assert "tenant_id" in snippet


def test_aggregator_deny_reasons_filters_deny_kill():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2000]
    assert "deny" in snippet and "kill" in snippet


def test_aggregator_deny_reasons_groups_by_reason():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2000]
    assert "reason" in snippet and ("group_by" in snippet or "GROUP BY" in snippet)


def test_aggregator_deny_reasons_handles_null_reason():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2000]
    assert "nullif" in snippet or "coalesce" in snippet or "unspecified" in snippet


def test_aggregator_deny_reasons_has_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2500]
    assert "count" in snippet


def test_aggregator_deny_reasons_has_pct():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2500]
    assert "pct" in snippet


def test_aggregator_deny_reasons_returns_reasons_key():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2500]
    assert '"reasons"' in snippet or "'reasons'" in snippet


def test_aggregator_deny_reasons_has_total_denied():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2500]
    assert "total_denied" in snippet


def test_aggregator_deny_reasons_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_deny_reasons")
    snippet = src[idx:idx + 2500]
    assert "computed_at" in snippet


# ── router.py: GET /logs/deny-reasons ────────────────────────────────────────

def test_router_has_deny_reasons_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "deny-reasons" in src


def test_router_deny_reasons_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_deny_reasons" in src


def test_router_deny_reasons_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("deny-reasons")
    snippet = src[idx:idx + 400]
    assert "days" in snippet


def test_router_deny_reasons_accepts_limit():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("deny-reasons")
    snippet = src[idx:idx + 400]
    assert "limit" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_deny_reasons_proxy():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "deny-reasons" in src


def test_gateway_deny_reasons_forwards_to_audit():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("deny-reasons")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/deny-reasons" in snippet


# ── api.js: getDenyReasons ───────────────────────────────────────────────────

def test_api_js_has_get_deny_reasons():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getDenyReasons" in src


def test_api_js_deny_reasons_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getDenyReasons")
    snippet = src[idx:idx + 200]
    assert "deny-reasons" in snippet


def test_api_js_deny_reasons_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getDenyReasons")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── PolicyAnalytics.jsx: DenyReasonsChart ────────────────────────────────────

def test_policy_analytics_has_deny_reasons_chart():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "DenyReasonsChart" in src


def test_policy_analytics_uses_get_deny_reasons():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "getDenyReasons" in src


def test_policy_analytics_has_deny_reasons_state():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "denyReasons" in src


def test_policy_analytics_chart_shows_reason():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("DenyReasonsChart")
    snippet = src[idx:idx + 2000]
    assert "reason" in snippet


def test_policy_analytics_chart_shows_count():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("DenyReasonsChart")
    snippet = src[idx:idx + 2000]
    assert "count" in snippet


def test_policy_analytics_chart_shows_pct():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("DenyReasonsChart")
    snippet = src[idx:idx + 2000]
    assert "pct" in snippet


def test_policy_analytics_chart_has_bar():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    idx = src.find("DenyReasonsChart")
    snippet = src[idx:idx + 2000]
    assert "width" in snippet or "barPct" in snippet


def test_policy_analytics_has_deny_reasons_heading():
    src = (ROOT / "ui/src/pages/PolicyAnalytics.jsx").read_text()
    assert "Top Deny Reasons" in src or "Deny Reasons" in src
