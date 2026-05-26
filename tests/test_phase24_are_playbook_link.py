"""Phase 24 source-contract tests — ARE→playbook link, internal/throttle endpoint, dead-code cleanup."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── are_executor.py: TRIGGER_PLAYBOOK action ──────────────────────────────────

def test_are_executor_has_trigger_playbook_fn():
    src = (ROOT / "services/api/are_executor.py").read_text()
    assert "_do_trigger_playbook" in src


def test_are_executor_trigger_calls_autonomy_service():
    src = (ROOT / "services/api/are_executor.py").read_text()
    idx = src.find("_do_trigger_playbook")
    snippet = src[idx:src.find("\nasync def ", idx + 1)] if "\nasync def " in src[idx:] else src[idx:]
    assert "_AUTONOMY_URL" in snippet or "autonomy" in snippet.lower()
    assert "/trigger" in snippet


def test_are_executor_trigger_uses_internal_secret():
    src = (ROOT / "services/api/are_executor.py").read_text()
    idx = src.find("_do_trigger_playbook")
    snippet = src[idx:src.find("\nasync def ", idx + 1)] if "\nasync def " in src[idx:] else src[idx:]
    assert "INTERNAL_SECRET" in snippet or "X-Internal-Secret" in snippet


def test_are_executor_trigger_wired_in_execute():
    src = (ROOT / "services/api/are_executor.py").read_text()
    idx = src.find("async def execute")
    body = src[idx:]
    assert "TRIGGER_PLAYBOOK" in body
    assert "_do_trigger_playbook" in body


def test_are_executor_trigger_is_fault_tolerant():
    src = (ROOT / "services/api/are_executor.py").read_text()
    idx = src.find("_do_trigger_playbook")
    fn_end = src.find("\nasync def ", idx + 1)
    fn_body = src[idx:fn_end] if fn_end != -1 else src[idx:]
    assert "except Exception" in fn_body


# ── api/main.py: /internal/throttle endpoint ─────────────────────────────────

def test_api_main_has_internal_throttle():
    src = (ROOT / "services/api/main.py").read_text()
    assert "/internal/throttle" in src


def test_api_main_throttle_writes_redis_key():
    src = (ROOT / "services/api/main.py").read_text()
    idx = src.find("/internal/throttle")
    snippet = src[idx:idx + 1200]
    assert "setex" in snippet
    assert "throttle" in snippet


def test_api_main_throttle_returns_status():
    src = (ROOT / "services/api/main.py").read_text()
    idx = src.find("/internal/throttle")
    snippet = src[idx:idx + 1200]
    assert '"throttled"' in snippet or "'throttled'" in snippet


# ── playbooks.py: dead _simulate_action removed ───────────────────────────────

def test_playbooks_simulate_action_removed():
    src = (ROOT / "services/autonomy/playbooks.py").read_text()
    assert "_simulate_action" not in src


def test_playbooks_execute_step_still_called():
    src = (ROOT / "services/autonomy/playbooks.py").read_text()
    assert "_execute_step" in src or "execute_step" in src


# ── AutoResponse.jsx: TRIGGER_PLAYBOOK action type ───────────────────────────

def test_auto_response_has_trigger_playbook_action():
    src = (ROOT / "ui/src/pages/AutoResponse.jsx").read_text()
    assert "TRIGGER_PLAYBOOK" in src


def test_auto_response_trigger_has_playbook_id_input():
    src = (ROOT / "ui/src/pages/AutoResponse.jsx").read_text()
    assert "playbook_id" in src
    # Use the second occurrence — the JSX conditional render, not the ACTION_TYPES definition
    first = src.find("TRIGGER_PLAYBOOK")
    idx = src.find("TRIGGER_PLAYBOOK", first + 1)
    vicinity = src[idx:idx + 500]
    assert "playbook_id" in vicinity


def test_auto_response_trigger_has_label():
    src = (ROOT / "ui/src/pages/AutoResponse.jsx").read_text()
    assert "Trigger Playbook" in src
