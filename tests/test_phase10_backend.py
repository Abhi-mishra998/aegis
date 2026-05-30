"""
Phase 10 backend source-contract tests.

These tests verify file-level contracts only — no imports, no running server.
They check that the GET /playbooks/stats endpoint exists in the gateway with
correct structure, response fields, sub-call patterns, and graceful error handling.
"""
from __future__ import annotations

from pathlib import Path

ROOT    = Path(__file__).parent.parent
# /playbooks/stats lives in the gateway's playbooks sub-router
# (services/gateway/routers/proxies.py) — extracted out of the monolithic
# main.py in sprint-2.10. Contract checks below scan both files so they
# survive future sub-router splits.
GATEWAY_FILES = [
    ROOT / "services/gateway/main.py",
    ROOT / "services/gateway/routers/proxies.py",
]
GATEWAY = GATEWAY_FILES[0]  # legacy alias for any test that takes a single Path


# ── helpers ────────────────────────────────────────────────────────────────

def _read(path: Path | list[Path] | None = None) -> str:
    if path is None:
        path = GATEWAY_FILES
    if isinstance(path, list):
        return "\n".join(p.read_text(encoding="utf-8") for p in path if p.exists())
    return path.read_text(encoding="utf-8")


def _stats_block(src: str) -> str:
    """Extract the source text of get_playbooks_stats function."""
    idx = src.find("get_playbooks_stats")
    assert idx != -1, "get_playbooks_stats not found in gateway"
    return src[idx: idx + 4000]


# ─────────────────────────────────────────────────────────────
# 1. Route declaration exists
# ─────────────────────────────────────────────────────────────

def test_gateway_has_playbooks_stats_route():
    src = _read()
    assert '"/playbooks/stats"' in src, \
        'GET /playbooks/stats route string not found in services/gateway/main.py'


def test_gateway_playbooks_stats_function_defined():
    src = _read()
    assert 'get_playbooks_stats' in src, \
        'get_playbooks_stats function not found in services/gateway/main.py'


# ─────────────────────────────────────────────────────────────
# 2. Response shape — all required fields present
# ─────────────────────────────────────────────────────────────

def test_stats_response_has_total_installed():
    src = _read()
    block = _stats_block(src)
    assert 'total_installed' in block, \
        'total_installed field missing from get_playbooks_stats response'


def test_stats_response_has_total_templates():
    src = _read()
    block = _stats_block(src)
    assert 'total_templates' in block, \
        'total_templates field missing from get_playbooks_stats response'


def test_stats_response_has_active():
    src = _read()
    block = _stats_block(src)
    assert '"active"' in block or "'active'" in block or 'active' in block, \
        'active field missing from get_playbooks_stats response'


def test_stats_response_has_triggers_24h():
    src = _read()
    block = _stats_block(src)
    assert 'triggers_24h' in block, \
        'triggers_24h field missing from get_playbooks_stats response'


def test_stats_response_has_last_trigger_at():
    src = _read()
    block = _stats_block(src)
    assert 'last_trigger_at' in block, \
        'last_trigger_at field missing from get_playbooks_stats response'


# ─────────────────────────────────────────────────────────────
# 3. Sub-calls — function fetches /playbooks and /templates
# ─────────────────────────────────────────────────────────────

def test_stats_calls_playbooks_list():
    src = _read()
    block = _stats_block(src)
    assert '/autonomy/playbooks' in block, \
        'get_playbooks_stats does not call /autonomy/playbooks in gateway'


def test_stats_calls_playbooks_templates():
    src = _read()
    block = _stats_block(src)
    assert '/autonomy/playbooks/templates' in block, \
        'get_playbooks_stats does not call /autonomy/playbooks/templates in gateway'


# ─────────────────────────────────────────────────────────────
# 4. Graceful failure — try/except blocks protect sub-calls
# ─────────────────────────────────────────────────────────────

def test_stats_graceful_failure_has_except_blocks():
    src = _read()
    block = _stats_block(src)
    assert block.count('except Exception') >= 1, \
        'get_playbooks_stats must have at least one except block for graceful sub-call failure'


def test_stats_returns_zeros_on_failure():
    src = _read()
    block = _stats_block(src)
    # Zeros are the fallback defaults (total_installed = 0, etc.)
    assert '= 0' in block, \
        'get_playbooks_stats must initialise counters to 0 for graceful zero-fallback on failure'


# ─────────────────────────────────────────────────────────────
# 5. Route ordering — /stats declared before /{pid} to avoid conflict
# ─────────────────────────────────────────────────────────────

def test_stats_route_declared_before_pid_route():
    src = _read()
    stats_pos = src.find('"/playbooks/stats"')
    pid_pos   = src.find('"/playbooks/{pid}"')
    assert stats_pos != -1, '"/playbooks/stats" route not found'
    assert pid_pos   != -1, '"/playbooks/{pid}" route not found'
    assert stats_pos < pid_pos, \
        '/playbooks/stats must be declared before /playbooks/{pid} to avoid route shadowing'


# ─────────────────────────────────────────────────────────────
# 6. Existing playbooks routes still present (no regressions)
# ─────────────────────────────────────────────────────────────

def test_existing_playbooks_list_route_still_present():
    src = _read()
    assert 'list_playbooks_proxy' in src, \
        'Existing list_playbooks_proxy route was removed — regression'


def test_existing_playbooks_templates_route_still_present():
    src = _read()
    assert 'get_playbook_templates_proxy' in src, \
        'Existing get_playbook_templates_proxy route was removed — regression'


def test_existing_playbooks_create_route_still_present():
    src = _read()
    assert 'create_playbook_proxy' in src, \
        'Existing create_playbook_proxy route was removed — regression'
