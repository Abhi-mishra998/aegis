"""Phase 22 source-contract tests — SDK integration packages + DeveloperPanel guide."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── integrations/README.md ────────────────────────────────────────────────────

def test_integrations_readme_exists():
    assert (ROOT / "integrations/README.md").exists()


def test_integrations_readme_covers_langchain():
    src = (ROOT / "integrations/README.md").read_text()
    assert "aegis-langchain" in src
    assert "AegisMiddleware" in src


def test_integrations_readme_covers_openai():
    src = (ROOT / "integrations/README.md").read_text()
    assert "aegis-openai" in src
    assert "AegisOpenAI" in src


def test_integrations_readme_covers_anthropic():
    src = (ROOT / "integrations/README.md").read_text()
    assert "aegis-anthropic" in src
    assert "AegisAnthropic" in src


def test_integrations_readme_documents_env_vars():
    src = (ROOT / "integrations/README.md").read_text()
    assert "AEGIS_API_KEY" in src
    assert "AEGIS_TENANT_ID" in src


def test_integrations_readme_documents_fail_open():
    src = (ROOT / "integrations/README.md").read_text()
    assert "fail-open" in src or "fail_open" in src


# ── docs/quickstart.md ───────────────────────────────────────────────────────

def test_quickstart_doc_exists():
    assert (ROOT / "docs/quickstart.md").exists()


def test_quickstart_covers_api_key_creation():
    src = (ROOT / "docs/quickstart.md").read_text()
    assert "API key" in src or "api_key" in src
    assert "acp_" in src


def test_quickstart_has_langchain_example():
    src = (ROOT / "docs/quickstart.md").read_text()
    assert "aegis-langchain" in src
    assert "AegisMiddleware" in src


def test_quickstart_has_openai_example():
    src = (ROOT / "docs/quickstart.md").read_text()
    assert "aegis-openai" in src
    assert "AegisOpenAI" in src


def test_quickstart_has_anthropic_example():
    src = (ROOT / "docs/quickstart.md").read_text()
    assert "aegis-anthropic" in src
    assert "AegisAnthropic" in src


def test_quickstart_references_audit_logs():
    src = (ROOT / "docs/quickstart.md").read_text()
    assert "Audit" in src


# ── DeveloperPanel.jsx: integration section ──────────────────────────────────

def test_developer_panel_shows_framework_integrations():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "Framework Integrations" in src or "framework integrations" in src.lower()


def test_developer_panel_shows_langchain_install():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "pip install aegis-langchain" in src


def test_developer_panel_shows_openai_install():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "pip install aegis-openai" in src


def test_developer_panel_shows_anthropic_install():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "pip install aegis-anthropic" in src


def test_developer_panel_shows_aegis_middleware():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "AegisMiddleware" in src


def test_developer_panel_shows_aegis_openai():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "AegisOpenAI" in src


def test_developer_panel_shows_aegis_anthropic():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "AegisAnthropic" in src
