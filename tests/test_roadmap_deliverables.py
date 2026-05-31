"""
Source-contract tests for the COMPREHENSIVE_ROADMAP.md 15-day deliverables.

Each group verifies one Day's deliverable is present and structurally correct.
No live services required — tests read file contents as strings.
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
UI   = ROOT / "ui" / "src"


# ══════════════════════════════════════════════════════════════════════════════
# DAY 1-2 — SSE live decision feed + Real autonomous agent
# ══════════════════════════════════════════════════════════════════════════════

def test_gateway_publishes_tool_executed_event():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "tool_executed" in src


def test_gateway_sse_push_includes_risk_and_action():
    src = (ROOT / "services/gateway/main.py").read_text()
    idx = src.find("tool_executed")
    snippet = src[idx:idx + 1200]
    assert "risk" in snippet
    assert "action" in snippet


def test_gateway_sse_stream_endpoint_exists():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/events/stream" in src


def test_observability_uses_eventbus_for_sse():
    src = (UI / "pages/Observability.jsx").read_text()
    assert "eventBus" in src
    assert "tool_executed" in src


def test_observability_no_setinterval_for_decisions():
    src = (UI / "pages/Observability.jsx").read_text()
    # Metrics poll every 5min — that's fine. But decision feed must be SSE-only.
    assert "setInterval(fetchSummary" not in src or "300_000" in src


def test_usesse_hook_exists():
    assert (UI / "hooks/useSSE.js").exists()


def test_usesse_has_exponential_backoff():
    src = (UI / "hooks/useSSE.js").read_text()
    assert "backoff" in src or "MAX_BACKOFF" in src


def test_autonomous_agent_demo_exists():
    assert (ROOT / "demos/live_agent/autonomous_agent.py").exists()


def test_autonomous_agent_is_real_not_scripted():
    src = (ROOT / "demos/live_agent/autonomous_agent.py").read_text()
    assert "anthropic" in src.lower()
    assert "acp_check" in src or "execute" in src


def test_autonomous_agent_has_task_categories():
    src = (ROOT / "demos/live_agent/autonomous_agent.py").read_text()
    assert "safe" in src
    assert "dangerous" in src


def test_autonomous_agent_readme_exists():
    assert (ROOT / "demos/live_agent/README.md").exists()


def test_autonomous_agent_env_example_exists():
    assert (ROOT / "demos/live_agent/.env.example").exists()


def test_autonomous_agent_env_has_required_keys():
    src = (ROOT / "demos/live_agent/.env.example").read_text()
    assert "ANTHROPIC_API_KEY" in src
    assert "ACP_GATEWAY_URL" in src


# ══════════════════════════════════════════════════════════════════════════════
# DAY 3-4 — API Key Management
# ══════════════════════════════════════════════════════════════════════════════

def test_api_key_router_exists():
    assert (ROOT / "services/api/router/api_key.py").exists()


def test_api_key_router_has_create():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "create_api_key" in src or 'post(\n    "",' in src or '@router.post' in src


def test_api_key_router_has_list():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "list_api_keys" in src or "list_for_tenant" in src


def test_api_key_router_has_revoke():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "revoke_api_key" in src or "deactivate" in src


def test_gateway_proxies_api_keys():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "/api-keys" in src


def test_api_js_has_api_key_methods():
    src = (UI / "services/api.js").read_text()
    assert "getApiKeys" in src
    assert "createApiKey" in src
    assert "revokeApiKey" in src


def test_developer_panel_has_api_key_tab():
    src = (UI / "pages/DeveloperPanel.jsx").read_text()
    assert "API Keys" in src
    assert "api-keys" in src or "getApiKeys" in src


def test_developer_panel_shows_key_once():
    src = (UI / "pages/DeveloperPanel.jsx").read_text()
    assert "once" in src.lower() or "shown" in src.lower() or "copy" in src.lower()


def test_developer_panel_has_revoke_button():
    src = (UI / "pages/DeveloperPanel.jsx").read_text()
    assert "revoke" in src.lower() or "Revoke" in src or "revokeApiKey" in src


# ══════════════════════════════════════════════════════════════════════════════
# DAY 5-6 — SDK Packages
# ══════════════════════════════════════════════════════════════════════════════

def test_aegis_langchain_package_exists():
    assert (ROOT / "integrations/aegis-langchain/aegis_langchain/__init__.py").exists()


def test_aegis_langchain_has_middleware_class():
    src = (ROOT / "integrations/aegis-langchain/aegis_langchain/__init__.py").read_text()
    assert "AegisMiddleware" in src or "aegis" in src.lower()


def test_aegis_langchain_calls_execute():
    src = (ROOT / "integrations/aegis-langchain/aegis_langchain/__init__.py").read_text()
    assert "execute" in src or "/execute" in src


def test_aegis_anthropic_package_exists():
    assert (ROOT / "integrations/aegis-anthropic/aegis_anthropic/__init__.py").exists()


def test_aegis_anthropic_has_client_wrapper():
    src = (ROOT / "integrations/aegis-anthropic/aegis_anthropic/__init__.py").read_text()
    assert "AegisAnthropic" in src or "anthropic" in src.lower()


def test_aegis_openai_package_exists():
    assert (ROOT / "integrations/aegis-openai/aegis_openai/__init__.py").exists()


def test_aegis_openai_has_client_wrapper():
    src = (ROOT / "integrations/aegis-openai/aegis_openai/__init__.py").read_text()
    assert "openai" in src.lower() or "AegisOpenAI" in src


def test_integrations_have_setup_py():
    for pkg in ["aegis-langchain", "aegis-anthropic", "aegis-openai"]:
        assert (ROOT / "integrations" / pkg / "setup.py").exists(), f"Missing setup.py for {pkg}"


def test_quickstart_doc_exists():
    assert (ROOT / "docs/quickstart.md").exists()


# ══════════════════════════════════════════════════════════════════════════════
# DAY 7-8 — SSO / OIDC
# ══════════════════════════════════════════════════════════════════════════════

def test_oidc_module_exists():
    assert (ROOT / "services/identity/oidc.py").exists()


def test_oidc_supports_multiple_providers():
    src = (ROOT / "services/identity/oidc.py").read_text()
    providers = sum(1 for p in ["google", "microsoft", "okta"] if p in src.lower())
    assert providers >= 2


def test_identity_router_has_sso_callback():
    src = (ROOT / "services/identity/router.py").read_text()
    assert "sso" in src and "callback" in src


def test_identity_router_has_sso_providers_list():
    src = (ROOT / "services/identity/router.py").read_text()
    assert "sso/providers" in src or "providers" in src


def test_login_page_has_sso_buttons():
    src = (UI / "pages/Login.jsx").read_text()
    assert "sso" in src.lower() or "SSO" in src


def test_login_page_loads_sso_providers():
    src = (UI / "pages/Login.jsx").read_text()
    assert "getSSOProviders" in src or "sso/providers" in src


def test_login_page_has_google_button():
    src = (UI / "pages/Login.jsx").read_text()
    assert "google" in src.lower()


def test_login_page_has_microsoft_button():
    src = (UI / "pages/Login.jsx").read_text()
    assert "microsoft" in src.lower()


# ══════════════════════════════════════════════════════════════════════════════
# DAY 9-10 — EU AI Act PDF Compliance Export
# ══════════════════════════════════════════════════════════════════════════════

def test_pdf_export_module_exists():
    assert (ROOT / "services/audit/pdf_export.py").exists()


def test_pdf_export_uses_reportlab():
    src = (ROOT / "services/audit/pdf_export.py").read_text()
    assert "reportlab" in src


def test_pdf_export_generates_bytes():
    src = (ROOT / "services/audit/pdf_export.py").read_text()
    assert "BytesIO" in src or "bytes" in src.lower()


def test_compliance_router_has_export_endpoint():
    src = (ROOT / "services/audit/compliance.py").read_text()
    assert "/export" in src or "compliance_export" in src


def test_gateway_proxies_compliance_export():
    # /compliance/export extracted from main.py to routers/compliance.py
    # in sprint-5.
    src = (
        (ROOT / "services/gateway/main.py").read_text()
        + (ROOT / "services/gateway/routers/compliance.py").read_text()
    )
    assert "compliance/export" in src


def test_compliance_page_exists():
    assert (UI / "pages/Compliance.jsx").exists()


def test_compliance_page_has_export_button():
    src = (UI / "pages/Compliance.jsx").read_text()
    assert "Export" in src or "export" in src


def test_compliance_page_handles_pdf_download():
    src = (UI / "pages/Compliance.jsx").read_text()
    assert "blob" in src or "download" in src.lower()


def test_compliance_page_supports_eu_ai_act():
    src = (UI / "pages/Compliance.jsx").read_text()
    assert "EU AI Act" in src or "EU_AI_ACT" in src


def test_reportlab_in_server_extras():
    src = (ROOT / "pyproject.toml").read_text()
    assert "reportlab" in src


# ══════════════════════════════════════════════════════════════════════════════
# DAY 11-12 — Visual Policy Builder
# ══════════════════════════════════════════════════════════════════════════════

def test_policy_builder_page_exists():
    assert (UI / "pages/PolicyBuilder.jsx").exists()


def test_policy_builder_is_not_stub():
    src = (UI / "pages/PolicyBuilder.jsx").read_text()
    assert len(src) > 2000, "PolicyBuilder.jsx appears to be a stub"


def test_policy_builder_has_rego_preview():
    src = (UI / "pages/PolicyBuilder.jsx").read_text()
    assert "rego" in src.lower() or "Rego" in src


def test_policy_builder_has_test_step():
    src = (UI / "pages/PolicyBuilder.jsx").read_text()
    assert "test" in src.lower()


def test_policy_builder_has_activate():
    src = (UI / "pages/PolicyBuilder.jsx").read_text()
    assert "activ" in src.lower() or "upload" in src.lower()


def test_policy_service_has_test_endpoint():
    src = (ROOT / "services/policy/router.py").read_text()
    assert "policy/test" in src or "policy_test" in src


def test_policy_service_has_upload_endpoint():
    src = (ROOT / "services/policy/router.py").read_text()
    assert "policy/upload" in src or "policy_upload" in src or "upload" in src


def _gateway_policy_src() -> str:
    """/policy/* extracted from main.py to routers/policy.py in sprint-5."""
    return (
        (ROOT / "services/gateway/main.py").read_text()
        + (ROOT / "services/gateway/routers/policy.py").read_text()
    )


def test_gateway_proxies_policy_test():
    assert "policy/test" in _gateway_policy_src()


def test_gateway_proxies_policy_upload():
    assert "policy/upload" in _gateway_policy_src()


def test_policy_builder_routed_in_app():
    src = (UI / "App.jsx").read_text()
    assert "PolicyBuilder" in src
    assert "policy-builder" in src


# ══════════════════════════════════════════════════════════════════════════════
# DAY 13-14 — Auto-Remediation Playbooks
# ══════════════════════════════════════════════════════════════════════════════

def test_playbooks_module_exists():
    assert (ROOT / "services/autonomy/playbooks.py").exists()


def test_playbooks_has_execute_function():
    src = (ROOT / "services/autonomy/playbooks.py").read_text()
    assert "execute_playbook" in src


def test_playbooks_has_slack_action():
    src = (ROOT / "services/autonomy/playbooks.py").read_text()
    assert "slack" in src.lower() or "notify" in src.lower()


def test_playbooks_has_kill_agent_action():
    src = (ROOT / "services/autonomy/playbooks.py").read_text()
    assert "kill" in src.lower() or "quarantine" in src.lower()


def test_playbooks_records_execution():
    src = (ROOT / "services/autonomy/playbooks.py").read_text()
    assert "playbook_run" in src or "execution_log" in src


def test_auto_response_page_exists():
    assert (UI / "pages/AutoResponse.jsx").exists()


def test_auto_response_is_not_stub():
    src = (UI / "pages/AutoResponse.jsx").read_text()
    assert len(src) > 2000, "AutoResponse.jsx appears to be a stub"


def test_auto_response_has_playbook_list():
    src = (UI / "pages/AutoResponse.jsx").read_text()
    assert "playbook" in src.lower()


def test_auto_response_has_create_button():
    src = (UI / "pages/AutoResponse.jsx").read_text()
    assert "create" in src.lower() or "new" in src.lower() or "Create" in src


def test_gateway_proxies_playbooks():
    src = (ROOT / "services/gateway/main.py").read_text()
    assert "playbooks" in src


def test_auto_response_routed_in_app():
    src = (UI / "App.jsx").read_text()
    assert "AutoResponse" in src
    assert "auto-response" in src


# ══════════════════════════════════════════════════════════════════════════════
# DAY 15 — Pricing Page + Traffic Generation
# ══════════════════════════════════════════════════════════════════════════════

def test_pricing_page_exists():
    assert (UI / "pages/Pricing.jsx").exists()


def test_pricing_page_is_public():
    src = (UI / "App.jsx").read_text()
    idx = src.find('path="/pricing"')
    snippet = src[idx:idx + 100]
    assert "ProtectedRoute" not in snippet, "Pricing page should be public (no ProtectedRoute)"


def test_pricing_page_has_three_tiers():
    # 2026-05-29 rewrite: the Pricing.jsx component is now the Open Source
    # landing page. The three-tier paywall (Starter/Professional/Enterprise)
    # was removed in favor of Apache 2.0 / self-host positioning. The test
    # is retained under the old name to keep CI history continuous; the
    # assertion now validates the OSS reframing.
    src = (UI / "pages/Pricing.jsx").read_text()
    assert "Apache 2.0" in src
    assert "Self-hostable" in src or "self-host" in src.lower()
    assert "github.com/Abhi-mishra998/aegis" in src


def test_pricing_page_has_monthly_prices():
    # 2026-05-29 rewrite: dollar prices removed with the paywall. The page
    # now shows the self-host quickstart instead. Test validates the OSS
    # quickstart is present.
    src = (UI / "pages/Pricing.jsx").read_text()
    assert "git clone" in src
    assert "docker compose" in src


def test_pricing_page_mentions_eu_ai_act():
    src = (UI / "pages/Pricing.jsx").read_text()
    assert "EU AI Act" in src or "compliance" in src.lower()


def test_traffic_generation_script_exists():
    assert (ROOT / "scripts/generate_real_traffic.py").exists()


def test_traffic_script_uses_autonomous_agent():
    src = (ROOT / "scripts/generate_real_traffic.py").read_text()
    assert "autonomous" in src.lower() or "live_agent" in src.lower() or "acp" in src.lower()


# ══════════════════════════════════════════════════════════════════════════════
# CROSS-CUTTING — The 8-minute demo prerequisites
# ══════════════════════════════════════════════════════════════════════════════

def test_observability_shows_live_feed():
    src = (UI / "pages/Observability.jsx").read_text()
    assert "Live Decision Feed" in src or "decision" in src.lower()


def test_observability_routed_in_app():
    src = (UI / "App.jsx").read_text()
    assert "/observability" in src


def test_compliance_routed_in_app():
    src = (UI / "App.jsx").read_text()
    assert "/compliance" in src


def test_pricing_routed_in_app():
    src = (UI / "App.jsx").read_text()
    assert "/pricing" in src


def test_demo_8min_script_prereqs_all_present():
    """All files the 8-minute demo script references must exist."""
    required = [
        ROOT / "demos/live_agent/autonomous_agent.py",
        ROOT / "services/audit/pdf_export.py",
        ROOT / "services/audit/compliance.py",
        UI / "pages/Observability.jsx",
        UI / "pages/Compliance.jsx",
        UI / "pages/Pricing.jsx",
    ]
    missing = [str(p) for p in required if not p.exists()]
    assert not missing, f"Demo prereqs missing: {missing}"
