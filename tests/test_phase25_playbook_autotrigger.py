"""Phase 25 source-contract tests — incident-driven playbook auto-trigger."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── incident_watcher.py: module structure ─────────────────────────────────────

def test_incident_watcher_exists():
    assert (ROOT / "services/autonomy/incident_watcher.py").exists()


def test_incident_watcher_has_matches_conditions():
    src = (ROOT / "services/autonomy/incident_watcher.py").read_text()
    assert "_matches_conditions" in src


def test_incident_watcher_has_run_function():
    src = (ROOT / "services/autonomy/incident_watcher.py").read_text()
    assert "run_incident_watcher" in src


def test_incident_watcher_uses_watcher_group():
    src = (ROOT / "services/autonomy/incident_watcher.py").read_text()
    assert "autonomy-playbook-watcher" in src


def test_incident_watcher_reads_incident_stream():
    src = (ROOT / "services/autonomy/incident_watcher.py").read_text()
    assert "acp:incidents:queue" in src


def test_incident_watcher_handles_cancellation():
    src = (ROOT / "services/autonomy/incident_watcher.py").read_text()
    assert "CancelledError" in src


def test_incident_watcher_fires_execute_playbook():
    src = (ROOT / "services/autonomy/incident_watcher.py").read_text()
    assert "execute_playbook" in src


# ── _matches_conditions logic (pure-function tests via import) ────────────────

def test_matches_conditions_risk_gte():
    from services.autonomy.incident_watcher import _matches_conditions
    assert _matches_conditions(
        {"risk_score": 0.95, "severity": "CRITICAL"},
        {"risk_score": {"gte": 0.9}},
    )
    assert not _matches_conditions(
        {"risk_score": 0.5},
        {"risk_score": {"gte": 0.9}},
    )


def test_matches_conditions_risk_lte():
    from services.autonomy.incident_watcher import _matches_conditions
    assert _matches_conditions({"risk_score": 0.3}, {"risk_score": {"lte": 0.5}})
    assert not _matches_conditions({"risk_score": 0.8}, {"risk_score": {"lte": 0.5}})


def test_matches_conditions_severity():
    from services.autonomy.incident_watcher import _matches_conditions
    assert _matches_conditions({"severity": "CRITICAL", "risk_score": 0}, {"severity": "CRITICAL"})
    assert not _matches_conditions({"severity": "LOW", "risk_score": 0}, {"severity": "CRITICAL"})


def test_matches_conditions_tool():
    from services.autonomy.incident_watcher import _matches_conditions
    assert _matches_conditions({"tool": "run_sql", "risk_score": 0}, {"tool": "run_sql"})
    assert not _matches_conditions({"tool": "read_file", "risk_score": 0}, {"tool": "run_sql"})


def test_matches_conditions_finding():
    from services.autonomy.incident_watcher import _matches_conditions
    assert _matches_conditions(
        {"findings": ["sql_injection"], "risk_score": 0},
        {"finding": "sql_injection"},
    )
    assert not _matches_conditions(
        {"findings": ["anomaly"], "risk_score": 0},
        {"finding": "sql_injection"},
    )


def test_matches_conditions_findings_contains():
    from services.autonomy.incident_watcher import _matches_conditions
    assert _matches_conditions(
        {"findings": ["ddl_destruction", "sql_injection"], "risk_score": 0.8},
        {"findings_contains": ["sql_injection", "ddl_destruction"]},
    )
    assert not _matches_conditions(
        {"findings": ["anomaly"], "risk_score": 0.8},
        {"findings_contains": ["sql_injection"]},
    )


def test_matches_conditions_empty_returns_false():
    from services.autonomy.incident_watcher import _matches_conditions
    assert not _matches_conditions({"risk_score": 0.99}, {})


def test_matches_conditions_finding_via_trigger():
    from services.autonomy.incident_watcher import _matches_conditions
    # trigger field is checked as fallback when findings list is absent
    assert _matches_conditions(
        {"trigger": "sql_injection", "risk_score": 0.8},
        {"finding": "sql_injection"},
    )


# ── autonomy/main.py: watcher wired into lifespan ────────────────────────────

def test_autonomy_main_imports_watcher():
    src = (ROOT / "services/autonomy/main.py").read_text()
    assert "incident_watcher" in src
    assert "run_incident_watcher" in src


def test_autonomy_main_creates_watcher_task():
    src = (ROOT / "services/autonomy/main.py").read_text()
    assert "create_task" in src
    assert "run_incident_watcher" in src


def test_autonomy_main_cancels_watcher_on_shutdown():
    src = (ROOT / "services/autonomy/main.py").read_text()
    assert "watcher_task.cancel" in src or "cancel()" in src


# ── autonomy/router.py: autotrigger-stats endpoint ───────────────────────────

def test_router_has_autotrigger_stats_endpoint():
    src = (ROOT / "services/autonomy/router.py").read_text()
    assert "autotrigger-stats" in src


def test_router_autotrigger_stats_queries_triggered_by():
    src = (ROOT / "services/autonomy/router.py").read_text()
    idx = src.find("autotrigger-stats")
    snippet = src[idx:idx + 600]
    assert "triggered_by" in snippet
    assert '"auto"' in snippet or "'auto'" in snippet


# ── api.js: getAutotriggerStats wired ────────────────────────────────────────

def test_api_js_has_autotrigger_stats():
    src = (ROOT / "ui/src/services/api.js").read_text()
    assert "getAutotriggerStats" in src
    assert "autotrigger-stats" in src


# ── Playbooks.jsx: displays auto-trigger info ─────────────────────────────────

def test_playbooks_jsx_fetches_autotrigger_stats():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "getAutotriggerStats" in src
    assert "autoStatsMap" in src


def test_playbooks_jsx_passes_autostats_to_card():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "autoStats=" in src or "autoStats =" in src


def test_playbooks_jsx_shows_auto_fired_count():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "auto_count" in src
    assert "Auto-fired" in src or "auto-fired" in src


def test_playbooks_jsx_shows_watching_indicator():
    src = (ROOT / "ui/src/pages/Playbooks.jsx").read_text()
    assert "Watching for matching incidents" in src
