"""Phase 21 source-contract tests — scheduled report delivery worker + threat intel demo mode."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── report_delivery.py: worker module ────────────────────────────────────────

def test_report_delivery_module_exists():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "run_report_delivery_worker" in src


def test_report_delivery_polls_redis_trigger_keys():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "acp:report_trigger:" in src


def test_report_delivery_sends_via_smtp():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "smtplib" in src
    assert "_send_email_sync" in src


def test_report_delivery_runs_smtp_in_executor():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "run_in_executor" in src


def test_report_delivery_generates_pdf():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "_generate_pdf_for_report" in src


def test_report_delivery_supports_board_type():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert '"board"' in src or "'board'" in src
    assert "generate_board_report_pdf" in src


def test_report_delivery_supports_compliance_types():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "generate_compliance_pdf" in src
    assert '"compliance"' in src or "'compliance'" in src


def test_report_delivery_skips_silently_without_smtp():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "_is_smtp_configured" in src
    assert "report_delivery_skipped_no_smtp" in src


def test_report_delivery_cleans_up_trigger_key():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "redis.delete(trigger_key)" in src or "await redis.delete" in src


def test_report_delivery_updates_last_run_at():
    src = (ROOT / "services/audit/report_delivery.py").read_text()
    assert "last_run_at" in src


# ── audit/main.py: worker wired into lifespan ────────────────────────────────

def test_audit_main_imports_report_delivery():
    src = (ROOT / "services/audit/main.py").read_text()
    assert "run_report_delivery_worker" in src


def test_audit_main_starts_delivery_task():
    src = (ROOT / "services/audit/main.py").read_text()
    assert "delivery_task" in src
    assert "asyncio.create_task" in src


def test_audit_main_cancels_delivery_task_on_shutdown():
    src = (ROOT / "services/audit/main.py").read_text()
    idx = src.find("delivery_task.cancel()")
    assert idx != -1, "delivery_task must be cancelled on shutdown"


# ── threat_intel.py: demo_mode field ─────────────────────────────────────────

def test_threat_intel_demo_ip_has_demo_mode():
    src = (ROOT / "services/audit/threat_intel.py").read_text()
    idx = src.find("def _demo_ip")
    snippet = src[idx:idx + 400]
    assert '"demo_mode": True' in snippet or "'demo_mode': True" in snippet


def test_threat_intel_demo_domain_has_demo_mode():
    src = (ROOT / "services/audit/threat_intel.py").read_text()
    idx = src.find("def _demo_domain")
    snippet = src[idx:idx + 400]
    assert '"demo_mode": True' in snippet or "'demo_mode': True" in snippet


# ── ThreatIntel.jsx: demo mode banner ────────────────────────────────────────

def test_threat_intel_jsx_checks_demo_mode():
    src = (ROOT / "ui/src/pages/ThreatIntel.jsx").read_text()
    assert "demo_mode" in src


def test_threat_intel_jsx_shows_demo_banner():
    src = (ROOT / "ui/src/pages/ThreatIntel.jsx").read_text()
    assert "isDemoMode" in src
    assert "Demo mode" in src
