"""Phase 27 source-contract tests — decision root cause analysis (explain endpoint)."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── audit/router.py: explain endpoint ────────────────────────────────────────

def test_audit_router_has_explain_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "/explain" in src or "explain" in src


def test_audit_router_has_finding_labels():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "_FINDING_LABELS" in src


def test_audit_router_finding_labels_has_entries():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "pii_exfiltration" in src
    assert "high_risk" in src


def test_audit_router_explain_builds_explanation():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "explanation" in src


def test_audit_router_explain_returns_timeline():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "timeline" in src


def test_audit_router_explain_returns_findings():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "findings" in src


def test_audit_router_explain_returns_signals():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "signals" in src


def test_audit_router_explain_handles_request_id():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "request_id" in src


def test_audit_router_explain_extracts_risk_score():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "risk_score" in src


def test_audit_router_explain_policy_context():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "policy_context" in src


# ── gateway/main.py: proxy routes ─────────────────────────────────────────────

def test_gateway_proxies_explain_endpoint():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/explain" in src or "explain" in src


def test_gateway_proxies_cost_attribution():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "cost-attribution" in src


def test_gateway_proxies_autotrigger_stats():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "autotrigger-stats" in src


def test_gateway_explain_forwards_to_audit_service():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "AUDIT_SERVICE_URL" in src
    idx = src.find("explain")
    snippet = src[max(0, idx - 300):idx + 500]
    assert "AUDIT_SERVICE_URL" in snippet or "audit" in snippet.lower()


def test_gateway_cost_attribution_forwards_to_billing():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("cost-attribution")
    snippet = src[max(0, idx - 300):idx + 500]
    assert "USAGE_SERVICE_URL" in snippet or "billing" in snippet.lower()


def test_gateway_autotrigger_stats_forwards_to_autonomy():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("autotrigger-stats")
    snippet = src[max(0, idx - 300):idx + 500]
    assert "AUTONOMY_SERVICE_URL" in snippet or "autonomy" in snippet.lower()


# ── api.js: explainDecision ───────────────────────────────────────────────────

def test_api_js_has_explain_decision():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "explainDecision" in src


def test_api_js_explain_decision_calls_explain_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "explain" in src


# ── AuditLogs.jsx: ExplainPanel ───────────────────────────────────────────────

def test_audit_logs_has_explain_panel():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "ExplainPanel" in src


def test_audit_logs_has_explain_button():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "Explain" in src


def test_audit_logs_explain_shows_explanation():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "explanation" in src


def test_audit_logs_explain_shows_findings():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    idx = src.find("ExplainPanel")
    snippet = src[idx:idx + 3000]
    assert "findings" in snippet or "finding" in snippet.lower()


def test_audit_logs_explain_shows_timeline():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "timeline" in src


def test_audit_logs_explain_calls_explain_decision():
    src = (ROOT / "ui/src/pages/AuditLogs.jsx").read_text()
    assert "explainDecision" in src
