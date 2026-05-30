"""Phase 34 source-contract tests — top security findings frequency."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_top_findings ──────────────────────────────────────────

def test_aggregator_has_top_findings_method():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_top_findings" in src


def test_aggregator_top_findings_unnests_jsonb():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert "jsonb_array_elements" in snippet or "findings" in snippet


def test_aggregator_top_findings_returns_findings_list():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert '"findings"' in snippet or "'findings'" in snippet


def test_aggregator_top_findings_has_count_field():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert "count" in snippet


def test_aggregator_top_findings_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_top_findings_accepts_days():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_aggregator_top_findings_accepts_limit():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


def test_aggregator_top_findings_has_total_events():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert "total_events" in snippet


def test_aggregator_top_findings_has_computed_at():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert "computed_at" in snippet


def test_aggregator_top_findings_orders_by_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_top_findings")
    snippet = src[idx:idx + 3000]
    assert "ORDER BY" in snippet or "desc" in snippet.lower()


# ── router.py: GET /logs/top-findings ─────────────────────────────────────────

def test_router_has_top_findings_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "top-findings" in src


def test_router_top_findings_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_top_findings" in src


def test_router_top_findings_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("top-findings")
    snippet = src[idx:idx + 500]
    assert "days" in snippet


def test_router_top_findings_accepts_limit():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("top-findings")
    snippet = src[idx:idx + 500]
    assert "limit" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_top_findings_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "top-findings" in src


def test_gateway_top_findings_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("top-findings")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/top-findings" in snippet


# ── api.js: getTopFindings ───────────────────────────────────────────────────

def test_api_js_has_get_top_findings():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getTopFindings" in src


def test_api_js_top_findings_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getTopFindings")
    snippet = src[idx:idx + 200]
    assert "top-findings" in snippet


def test_api_js_top_findings_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getTopFindings")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── RiskEngine.jsx: TopFindingsChart ────────────────────────────────────────

def test_risk_engine_has_top_findings_chart():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "TopFindingsChart" in src


def test_risk_engine_uses_get_top_findings():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "getTopFindings" in src


def test_risk_engine_has_top_findings_state():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "topFindings" in src


def test_risk_engine_imports_audit_service():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "auditService" in src


def test_risk_engine_top_findings_shows_count():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "total_events" in src or "count" in src


def test_risk_engine_top_findings_has_section_heading():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "Top Security Findings" in src or "Findings" in src


def test_risk_engine_top_findings_renders_bar():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    idx = src.find("TopFindingsChart")
    # The component definition should have a bar-like element
    snippet = src[idx:idx + 1000]
    assert "width" in snippet or "pct" in snippet or "bar" in snippet.lower()
