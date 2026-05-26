"""
Source-contract tests for the real Slack/webhook execution layer.

No running server required — tests read file contents to verify
structural correctness.  All 8 tests mirror the playbook test style.
"""
from __future__ import annotations

import ast
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(rel_path: str) -> str:
    with open(os.path.join(_REPO_ROOT, rel_path)) as f:
        return f.read()


# ---------------------------------------------------------------------------
# 1. webhook_executor.py contains fire_slack
# ---------------------------------------------------------------------------

def test_webhook_executor_has_fire_slack():
    """webhook_executor.py defines an async function fire_slack."""
    source = _read("services/autonomy/webhook_executor.py")
    tree = ast.parse(source)
    async_fns = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert "fire_slack" in async_fns, (
        "webhook_executor.py does not define async fire_slack"
    )


# ---------------------------------------------------------------------------
# 2. webhook_executor.py contains fire_pagerduty
# ---------------------------------------------------------------------------

def test_webhook_executor_has_fire_pagerduty():
    """webhook_executor.py defines an async function fire_pagerduty."""
    source = _read("services/autonomy/webhook_executor.py")
    tree = ast.parse(source)
    async_fns = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert "fire_pagerduty" in async_fns, (
        "webhook_executor.py does not define async fire_pagerduty"
    )


# ---------------------------------------------------------------------------
# 3. webhook_executor.py contains execute_step
# ---------------------------------------------------------------------------

def test_webhook_executor_has_execute_step():
    """webhook_executor.py defines an async function execute_step."""
    source = _read("services/autonomy/webhook_executor.py")
    tree = ast.parse(source)
    async_fns = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
    }
    assert "execute_step" in async_fns, (
        "webhook_executor.py does not define async execute_step"
    )


# ---------------------------------------------------------------------------
# 4. execute_step routes SEND_ALERT by channel
# ---------------------------------------------------------------------------

def test_execute_step_routes_send_alert():
    """execute_step in webhook_executor.py handles SEND_ALERT with channel routing."""
    source = _read("services/autonomy/webhook_executor.py")
    # Must branch on SEND_ALERT action type
    assert "SEND_ALERT" in source, (
        "execute_step does not contain SEND_ALERT routing logic"
    )
    # Must handle at least slack and pagerduty channels
    assert "slack" in source, (
        "execute_step does not route to Slack"
    )
    assert "pagerduty" in source, (
        "execute_step does not route to PagerDuty"
    )
    # Must handle WEBHOOK action type
    assert "WEBHOOK" in source, (
        "execute_step does not handle WEBHOOK action type"
    )


# ---------------------------------------------------------------------------
# 5. playbooks.py imports execute_step (or _execute_step)
# ---------------------------------------------------------------------------

def test_playbooks_imports_execute_step():
    """playbooks.py imports execute_step from webhook_executor."""
    source = _read("services/autonomy/playbooks.py")
    assert "execute_step" in source or "_execute_step" in source, (
        "playbooks.py does not import or reference execute_step from webhook_executor"
    )
    assert "webhook_executor" in source, (
        "playbooks.py does not import from webhook_executor"
    )


# ---------------------------------------------------------------------------
# 6. autonomy/router.py contains /webhooks/config routes
# ---------------------------------------------------------------------------

def test_webhook_config_routes_in_router():
    """autonomy/router.py defines GET and POST /webhooks/config endpoints."""
    source = _read("services/autonomy/router.py")
    assert "/webhooks/config" in source, (
        "autonomy/router.py does not contain /webhooks/config route"
    )
    # Verify test routes exist
    assert "/webhooks/test/slack" in source, (
        "autonomy/router.py does not contain /webhooks/test/slack route"
    )
    assert "/webhooks/test/pagerduty" in source, (
        "autonomy/router.py does not contain /webhooks/test/pagerduty route"
    )
    assert "/webhooks/test/webhook" in source, (
        "autonomy/router.py does not contain /webhooks/test/webhook route"
    )


# ---------------------------------------------------------------------------
# 7. gateway/main.py proxies /webhooks/config
# ---------------------------------------------------------------------------

def test_gateway_proxies_webhook_config():
    """gateway/main.py contains proxy routes for /webhooks/config."""
    source = _read("services/gateway/main.py")
    assert "/webhooks/config" in source, (
        "gateway/main.py does not proxy /webhooks/config"
    )
    assert "/webhooks/test/slack" in source, (
        "gateway/main.py does not proxy /webhooks/test/slack"
    )
    assert "/webhooks/test/pagerduty" in source, (
        "gateway/main.py does not proxy /webhooks/test/pagerduty"
    )
    assert "/webhooks/test/webhook" in source, (
        "gateway/main.py does not proxy /webhooks/test/webhook"
    )


# ---------------------------------------------------------------------------
# 8. api.js exports webhookService
# ---------------------------------------------------------------------------

def test_api_js_has_webhook_service():
    """ui/src/services/api.js exports a webhookService object."""
    source = _read("ui/src/services/api.js")
    assert "webhookService" in source, (
        "ui/src/services/api.js does not export webhookService"
    )
    # Verify key methods exist
    assert "getConfig" in source, "webhookService missing getConfig method"
    assert "saveConfig" in source, "webhookService missing saveConfig method"
    assert "testSlack" in source, "webhookService missing testSlack method"
    assert "testPagerduty" in source, "webhookService missing testPagerduty method"
    assert "testWebhook" in source, "webhookService missing testWebhook method"
