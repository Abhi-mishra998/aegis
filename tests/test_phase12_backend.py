"""Phase 12 backend source-contract tests — fleet summary endpoint + security posture live data."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Registry router: GET /summary ────────────────────────────────────────────

def test_registry_router_has_summary_route():
    src = (ROOT / "services/registry/router.py").read_text()
    assert '"/summary"' in src


def test_registry_summary_aggregates_by_status():
    src = (ROOT / "services/registry/router.py").read_text()
    # Find the summary route section
    idx = src.find('"/summary"')
    snippet = src[idx:idx + 800]
    assert "status" in snippet
    assert "cnt" in snippet or "count" in snippet.lower()


def test_registry_summary_returns_quarantined_count():
    src = (ROOT / "services/registry/router.py").read_text()
    assert "quarantined" in src.lower()


def test_registry_summary_returns_high_risk():
    src = (ROOT / "services/registry/router.py").read_text()
    assert "high_risk" in src or "risk_level" in src


def test_registry_summary_filters_by_tenant():
    src = (ROOT / "services/registry/router.py").read_text()
    idx = src.find('"/summary"')
    snippet = src[idx:idx + 800]
    assert "tenant_id" in snippet


def test_registry_summary_excludes_deleted():
    src = (ROOT / "services/registry/router.py").read_text()
    idx = src.find('"/summary"')
    snippet = src[idx:idx + 800]
    assert "deleted_at" in snippet


# ── Gateway: GET /agents/summary proxy ───────────────────────────────────────

def test_gateway_has_agents_summary_route():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/agents/summary" in src


def test_gateway_agents_summary_proxies_to_registry():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("/agents/summary")
    snippet = src[max(0, idx - 50):idx + 300]
    assert "REGISTRY_SERVICE_URL" in snippet or "_trust_proxy" in snippet


# ── Security posture: live incidents + key rotation ──────────────────────────

def test_posture_queries_incidents_summary():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "incidents/summary" in src


def test_posture_no_hardcoded_open_incidents():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "open_incidents = 0  # placeholder" not in src


def test_posture_queries_transparency_keys():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "transparency/keys" in src
    # Should appear more than once — once for the route proxy, once in posture
    assert src.count("transparency/keys") >= 2


def test_posture_no_hardcoded_rotation_days():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "last_rotation_days_ago = 14  # placeholder" not in src


def test_posture_computes_rotation_from_created_at():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "created_at" in src
    assert "days" in src
