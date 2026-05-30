"""Phase 33 source-contract tests — agent peer benchmarking."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── aggregator.py: get_agent_peer_benchmark ───────────────────────────────────

def test_aggregator_has_peer_benchmark_method():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    assert "get_agent_peer_benchmark" in src


def test_aggregator_peer_benchmark_has_percentiles():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "percentiles" in snippet


def test_aggregator_peer_benchmark_has_deny_rate_percentile():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "deny_rate" in snippet


def test_aggregator_peer_benchmark_has_avg_risk_percentile():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "avg_risk" in snippet


def test_aggregator_peer_benchmark_has_call_volume_percentile():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "call_volume" in snippet


def test_aggregator_peer_benchmark_has_references():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "references" in snippet


def test_aggregator_peer_benchmark_has_p50():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "p50" in snippet


def test_aggregator_peer_benchmark_has_p95():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "p95" in snippet


def test_aggregator_peer_benchmark_has_peer_count():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "peer_count" in snippet


def test_aggregator_peer_benchmark_has_agent_stats():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "agent_stats" in snippet


def test_aggregator_peer_benchmark_filters_by_tenant():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "tenant_id" in snippet


def test_aggregator_peer_benchmark_groups_by_agent():
    src = (ROOT / "services/audit/aggregator.py").read_text()
    idx = src.find("get_agent_peer_benchmark")
    snippet = src[idx:idx + 3000]
    assert "agent_id" in snippet and ("group_by" in snippet or "AuditLog.agent_id" in snippet)


# ── router.py: GET /logs/peer-benchmark/{agent_id} ───────────────────────────

def test_router_has_peer_benchmark_endpoint():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "peer-benchmark" in src


def test_router_peer_benchmark_calls_aggregator():
    src = (ROOT / "services/audit/router.py").read_text()
    assert "get_agent_peer_benchmark" in src


def test_router_peer_benchmark_accepts_days():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("peer-benchmark")
    snippet = src[idx:idx + 600]
    assert "days" in snippet


def test_router_peer_benchmark_validates_uuid():
    src = (ROOT / "services/audit/router.py").read_text()
    idx = src.find("peer-benchmark")
    snippet = src[idx:idx + 600]
    assert "uuid.UUID" in snippet or "UUID" in snippet


# ── gateway/main.py: proxy ────────────────────────────────────────────────────

def test_gateway_has_peer_benchmark_proxy():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    assert "peer-benchmark" in src


def test_gateway_peer_benchmark_forwards_to_audit():
    src = ((ROOT / "services/gateway/routers/audit.py").read_text() + (ROOT / "services/gateway/main.py").read_text())
    idx = src.find("peer-benchmark")
    snippet = src[idx:idx + 400]
    assert "AUDIT_SERVICE_URL" in snippet or "logs/peer-benchmark" in snippet


# ── api.js: getPeerBenchmark ─────────────────────────────────────────────────

def test_api_js_has_get_peer_benchmark():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getPeerBenchmark" in src


def test_api_js_peer_benchmark_calls_correct_endpoint():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getPeerBenchmark")
    snippet = src[idx:idx + 200]
    assert "peer-benchmark" in snippet


def test_api_js_peer_benchmark_accepts_days():
    src = (ROOT / "ui/src/services/api.js").read_text()
    idx = src.find("getPeerBenchmark")
    snippet = src[idx:idx + 200]
    assert "days" in snippet


# ── AgentProfile.jsx: PeerBenchmarkPanel ────────────────────────────────────

def test_agent_profile_has_peer_benchmark_panel():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "PeerBenchmarkPanel" in src


def test_agent_profile_uses_get_peer_benchmark():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "getPeerBenchmark" in src


def test_agent_profile_has_benchmark_state():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "benchmark" in src and "setBenchmark" in src


def test_agent_profile_peer_benchmark_shows_percentile():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "percentile" in src or "Percentile" in src


def test_agent_profile_peer_benchmark_shows_deny_rate():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "Deny Rate" in src or "deny_rate" in src


def test_agent_profile_peer_benchmark_shows_peer_count():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "peer_count" in src or "peer" in src


def test_agent_profile_peer_benchmark_shows_references():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "p50" in src or "references" in src


def test_agent_profile_peer_benchmark_has_gauge():
    src = (ROOT / "ui/src/pages/AgentProfile.jsx").read_text()
    assert "PeerBenchmarkGauge" in src or "Gauge" in src
