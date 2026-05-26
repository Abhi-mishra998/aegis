"""Phase 15 UI source-contract tests — FlightRecorder filter UI, Observability insights refresh, RiskEngine SSE."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── FlightRecorder.jsx: tool + status filter inputs ───────────────────────────

def test_flight_recorder_has_tool_filter_input():
    src = (ROOT / "ui/src/pages/FlightRecorder.jsx").read_text()
    assert "filter.tool" in src


def test_flight_recorder_tool_filter_is_text_input():
    src = (ROOT / "ui/src/pages/FlightRecorder.jsx").read_text()
    assert 'type="text"' in src
    assert "Filter by tool" in src


def test_flight_recorder_has_status_filter_select():
    src = (ROOT / "ui/src/pages/FlightRecorder.jsx").read_text()
    assert "filter.status" in src


def test_flight_recorder_status_select_has_ok_option():
    src = (ROOT / "ui/src/pages/FlightRecorder.jsx").read_text()
    assert '"ok"' in src or "'ok'" in src


def test_flight_recorder_status_select_has_error_option():
    src = (ROOT / "ui/src/pages/FlightRecorder.jsx").read_text()
    assert '"error"' in src or "'error'" in src


def test_flight_recorder_filter_updates_on_change():
    src = (ROOT / "ui/src/pages/FlightRecorder.jsx").read_text()
    # Both inputs call setFilter with the new value
    assert src.count("setFilter") >= 3  # time buttons + tool input + status select


# ── Observability.jsx: periodic insights refresh ─────────────────────────────

def test_observability_has_insights_interval():
    src = (ROOT / "ui/src/pages/Observability.jsx").read_text()
    assert "insightsInterval" in src


def test_observability_insights_interval_is_60s():
    src = (ROOT / "ui/src/pages/Observability.jsx").read_text()
    assert "60_000" in src


def test_observability_insights_interval_clears_on_unmount():
    src = (ROOT / "ui/src/pages/Observability.jsx").read_text()
    assert "clearInterval(insightsInterval)" in src


def test_observability_insights_refresh_merges_live_items():
    src = (ROOT / "ui/src/pages/Observability.jsx").read_text()
    assert "liveItems" in src


def test_observability_insights_refresh_calls_get_insights():
    src = (ROOT / "ui/src/pages/Observability.jsx").read_text()
    # getInsights appears in both the initial load and the interval
    assert src.count("getInsights") >= 2


# ── RiskEngine.jsx: SSE-driven refresh ────────────────────────────────────────

def test_risk_engine_imports_event_bus():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "eventBus" in src


def test_risk_engine_listens_to_policy_decision():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "policy_decision" in src


def test_risk_engine_listens_to_tool_executed():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "tool_executed" in src


def test_risk_engine_debounces_sse_trigger():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    assert "debounce" in src.lower()


def test_risk_engine_cleans_up_sse_listeners():
    src = (ROOT / "ui/src/pages/RiskEngine.jsx").read_text()
    # Returns cleanup functions from eventBus.on
    assert "u1(); u2()" in src or ("u1()" in src and "u2()" in src)
