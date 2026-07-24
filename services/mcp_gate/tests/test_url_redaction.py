"""Tests for `_redact_url_credentials` — audit trail must not persist
basic-auth credentials embedded in URLs."""
from __future__ import annotations

import importlib


def _reload(monkeypatch):
    monkeypatch.setenv("MCP_GATE_BEARER_TOKEN", "test")
    monkeypatch.setenv("ENVIRONMENT", "development")
    from services.mcp_gate import router as r
    importlib.reload(r)
    return r


class TestRedactUrlCredentials:
    def test_basic_auth_stripped(self, monkeypatch):
        r = _reload(monkeypatch)
        assert r._redact_url_credentials(
            "https://user:pass@downstream.example/mcp"
        ) == "https://downstream.example/mcp"

    def test_user_only_stripped(self, monkeypatch):
        r = _reload(monkeypatch)
        assert r._redact_url_credentials(
            "https://user@downstream.example/mcp"
        ) == "https://downstream.example/mcp"

    def test_no_credentials_unchanged(self, monkeypatch):
        r = _reload(monkeypatch)
        url = "https://downstream.example/mcp"
        assert r._redact_url_credentials(url) == url

    def test_port_preserved(self, monkeypatch):
        r = _reload(monkeypatch)
        assert r._redact_url_credentials(
            "https://user:pass@downstream.example:8443/mcp"
        ) == "https://downstream.example:8443/mcp"

    def test_path_query_fragment_preserved(self, monkeypatch):
        r = _reload(monkeypatch)
        assert r._redact_url_credentials(
            "https://u:p@host/a/b?q=1&r=2#frag"
        ) == "https://host/a/b?q=1&r=2#frag"

    def test_at_in_path_not_confused_for_userinfo(self, monkeypatch):
        r = _reload(monkeypatch)
        # `@` in the path is a normal URL char — netloc doesn't contain it.
        assert r._redact_url_credentials(
            "https://downstream.example/user@handle"
        ) == "https://downstream.example/user@handle"

    def test_non_url_passthrough(self, monkeypatch):
        r = _reload(monkeypatch)
        assert r._redact_url_credentials("not-a-url") == "not-a-url"
        assert r._redact_url_credentials("") == ""

    def test_http_scheme_also_redacted(self, monkeypatch):
        r = _reload(monkeypatch)
        assert r._redact_url_credentials(
            "http://admin:secret@10.0.0.1/mcp"
        ) == "http://10.0.0.1/mcp"

    def test_empty_password_still_stripped(self, monkeypatch):
        """`user:@host` — colon present but empty password. Still user
        info, still stripped."""
        r = _reload(monkeypatch)
        assert r._redact_url_credentials(
            "https://user:@downstream.example/mcp"
        ) == "https://downstream.example/mcp"
