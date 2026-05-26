"""Phase 23 source-contract tests — playbook enforcement actions + live agent demo."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── webhook_executor.py: enforcement action implementations ──────────────────

def test_executor_has_kill_agent():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    assert "_do_kill_agent" in src


def test_executor_has_isolate_agent():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    assert "_do_isolate_agent" in src


def test_executor_has_block_tool():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    assert "_do_block_tool" in src


def test_executor_has_throttle():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    assert "_do_throttle" in src


def test_executor_has_revoke_key():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    assert "_do_revoke_key" in src


def test_executor_kill_agent_calls_registry():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    idx = src.find("_do_kill_agent")
    snippet = src[idx:idx + 400]
    assert "_REGISTRY_URL" in snippet or "REGISTRY_SERVICE_URL" in snippet or "registry" in snippet


def test_executor_block_tool_sends_deny_permission():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    idx = src.find("_do_block_tool")
    snippet = src[idx:idx + 400]
    assert "DENY" in snippet
    assert "permissions" in snippet


def test_executor_revoke_key_calls_api_service():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    idx = src.find("_do_revoke_key")
    snippet = src[idx:idx + 400]
    assert "_API_URL" in snippet or "API_SERVICE_URL" in snippet or "api-keys" in snippet


def test_execute_step_routes_kill_agent():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    idx = src.find("async def execute_step")
    snippet = src[idx:idx + 600]
    assert "KILL_AGENT" in snippet
    assert "_do_kill_agent" in snippet


def test_execute_step_routes_all_actions():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    idx = src.find("async def execute_step")
    snippet = src[idx:]  # full function to end of file
    for action in ("KILL_AGENT", "ISOLATE_AGENT", "BLOCK_TOOL", "THROTTLE", "REVOKE_KEY"):
        assert action in snippet, f"execute_step does not route {action}"


def test_executor_uses_internal_secret_header():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    assert "X-Internal-Secret" in src
    assert "_INTERNAL_SECRET" in src or "INTERNAL_SECRET" in src


def test_executor_never_raises_on_network_error():
    src = (ROOT / "services/autonomy/webhook_executor.py").read_text()
    import re
    do_fns = re.findall(r"async def (_do_\w+)", src)
    for fn in do_fns:
        fn_idx = src.find(f"async def {fn}")
        # Find next function boundary to get the full body
        next_fn = src.find("\nasync def ", fn_idx + 1)
        fn_body = src[fn_idx:next_fn] if next_fn != -1 else src[fn_idx:]
        assert "except Exception" in fn_body, f"{fn} must catch exceptions"


# ── live agent demo: setup files ──────────────────────────────────────────────

def test_live_agent_env_example_exists():
    assert (ROOT / "demos/live_agent/.env.example").exists()


def test_live_agent_env_example_has_required_vars():
    src = (ROOT / "demos/live_agent/.env.example").read_text()
    assert "ANTHROPIC_API_KEY" in src
    assert "ACP_GATEWAY_URL" in src
    assert "ACP_TENANT_ID" in src
    assert "ACP_API_KEY" in src


def test_live_agent_readme_exists():
    assert (ROOT / "demos/live_agent/README.md").exists()


def test_live_agent_readme_has_setup_steps():
    src = (ROOT / "demos/live_agent/README.md").read_text()
    assert "pip install" in src
    assert "autonomous_agent.py" in src


def test_live_agent_readme_references_dashboard():
    src = (ROOT / "demos/live_agent/README.md").read_text()
    assert "observability" in src.lower() or "dashboard" in src.lower()
