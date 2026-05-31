"""Phase 19 source-contract tests — API key management (services/api/ + gateway auth)."""
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ── Model: APIKey in services/api ────────────────────────────────────────────

def test_api_key_model_exists():
    src = (ROOT / "services/api/models/api_key.py").read_text()
    assert "class APIKey" in src


def test_api_key_model_has_key_prefix():
    src = (ROOT / "services/api/models/api_key.py").read_text()
    assert "key_prefix" in src


def test_api_key_model_has_key_hash():
    src = (ROOT / "services/api/models/api_key.py").read_text()
    assert "key_hash" in src


def test_api_key_model_has_active_flag():
    src = (ROOT / "services/api/models/api_key.py").read_text()
    assert "is_active" in src


# ── Router: CRUD endpoints in services/api ───────────────────────────────────

def test_router_has_create_endpoint():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "async def create_api_key" in src


def test_router_has_list_endpoint():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "async def list_api_keys" in src


def test_router_has_revoke_endpoint():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "async def revoke_api_key" in src


def test_router_has_validate_endpoint():
    src = (ROOT / "services/api/router/api_key.py").read_text()
    assert "async def validate_api_key" in src


# ── Repository: secure key generation ────────────────────────────────────────

def test_repository_uses_secrets_module():
    src = (ROOT / "services/api/repository/api_key.py").read_text()
    assert "secrets.token_urlsafe" in src


def test_repository_hashes_key_not_stored_plaintext():
    src = (ROOT / "services/api/repository/api_key.py").read_text()
    assert "hashlib" in src or "_hash_key" in src


def test_repository_key_uses_acp_prefix():
    src = (ROOT / "services/api/repository/api_key.py").read_text()
    assert '"acp_"' in src or "'acp_'" in src or 'f"acp_' in src


# ── Gateway: API key auth middleware ─────────────────────────────────────────

def test_gateway_auth_mixin_handles_api_key_bearer():
    src = (ROOT / "services/gateway/_mw_auth.py").read_text()
    assert 'startswith("acp_")' in src


def test_gateway_auth_mixin_handles_x_api_key_header():
    src = (ROOT / "services/gateway/_mw_auth.py").read_text()
    assert "X-API-Key" in src


def test_gateway_auth_validates_via_service():
    src = (ROOT / "services/gateway/_mw_auth.py").read_text()
    assert "validate_api_key" in src


def test_gateway_proxy_has_validate_route():
    # /api-keys/* extracted from main.py to routers/users.py in sprint-5.
    src = (
        (ROOT / "services/gateway/main.py").read_text()
        + (ROOT / "services/gateway/routers/users.py").read_text()
    )
    assert "/api-keys/validate" in src


# ── UI: DeveloperPanel wired to api-keys endpoints ───────────────────────────

def test_developer_panel_fetches_api_keys():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "getApiKeys" in src


def test_developer_panel_can_create_api_key():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "createApiKey" in src


def test_developer_panel_can_revoke_api_key():
    src = (ROOT / "ui/src/pages/DeveloperPanel.jsx").read_text()
    assert "revokeApiKey" in src
