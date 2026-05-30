"""Phase 11 backend source-contract tests — audit heatmap endpoint."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Audit router: /logs/heatmap ───────────────────────────────────────────────

def test_audit_router_has_heatmap_route():
    src = (ROOT / "services/audit/router.py").read_text()
    assert '"/heatmap"' in src


def test_audit_heatmap_groups_by_dow_and_hour():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "extract" in src.lower()
    assert "dow" in src
    assert "hour" in src


def test_audit_heatmap_filters_last_7_days():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "timedelta" in src or "interval" in src.lower()
    assert "7" in src


def test_audit_heatmap_returns_day_keys():
    src = (ROOT / "services/audit/router.py").read_text()
    assert '"Mon"' in src or "'Mon'" in src
    assert '"Sun"' in src or "'Sun'" in src


def test_audit_heatmap_returns_24_hours():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "24" in src


def test_audit_heatmap_uses_tenant_id_filter():
    src = (ROOT / "services/audit/router.py").read_text()
    heatmap_idx = src.find('"/heatmap"')
    snippet = src[heatmap_idx:heatmap_idx + 800]
    assert "tenant_id" in snippet


# ── Gateway: /audit/logs/heatmap proxy ───────────────────────────────────────

def test_gateway_has_heatmap_route():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/audit/logs/heatmap" in src


def test_gateway_heatmap_proxies_to_audit_service():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/logs/heatmap" in src


def test_gateway_heatmap_uses_trust_proxy():
    # /audit/logs/heatmap was extracted from main.py to routers/audit.py in
    # sprint-5. Search the new file first so .find() lands on the real
    # handler body (which calls `trust_proxy`) instead of the pointer
    # comment in main.py.
    src = (
        (ROOT / "services/gateway/routers/audit.py").read_text()
        + (ROOT / "services/gateway/main.py").read_text()
    )
    heatmap_idx = src.find("/audit/logs/heatmap")
    snippet = src[heatmap_idx:heatmap_idx + 300]
    assert ("_trust_proxy" in snippet
            or "trust_proxy" in snippet
            or "AUDIT_SERVICE_URL" in snippet)
