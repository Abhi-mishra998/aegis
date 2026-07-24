# arch-26 W4.1 — make `from services.X import Y` work in unit tests
# when pytest is run from any CWD. Docker test-runners set PYTHONPATH;
# a developer running `pytest tests/` locally didn't, and the
# pyproject.toml `pythonpath = ["."]` setting didn't reliably take
# effect across pytest versions. conftest.py at repo root runs BEFORE
# any test is collected, so the path is in place by collection time.
import os as _os
import sys as _sys
_REPO_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)


# Exclude locust/soak load tests from normal collection.
# Locust imports gevent which calls monkey.patch_all() at import time,
# patching ssl/socket after they've already been imported by asyncio —
# causes MonkeyPatchWarning and can break async tests in the same session.
# Run load tests separately: locust -f tests/load/locustfile.py
collect_ignore_glob = [
    "tests/load/*",
    "tests/load_test.py",
    "tests/e2e_test_flow.py",
]


# audit follow-up 2026-07-21: auto-mark the tests that need a live
# stack / UI bundle / OTEL collector as `integration` so they get
# deselected under the default `-m "not integration"` filter. The
# categories:
#
#   * ``test_phase*`` — source-contract asserts against `ui/src/**/*.jsx`
#     (frontend feature-presence checks). Live UI build required.
#   * ``test_roadmap_deliverables`` — same shape as test_phase*.
#   * ``test_demo_workspace_blocked_paths`` — round-trip against a real
#     Redis + mesh JWT signing keys.
#   * ``test_mesh_auth`` — mesh ES256 keypair required (`ACP_MESH_*`).
#   * ``test_n16_n20_ssrf_hardening`` — real Redis for backing store.
#   * ``test_api_key_auth`` — MockRedis fixture doesn't cover all methods.
#   * ``test_otel_pipeline`` — OTLP collector on localhost:4317.
#   * ``test_ei18_webhook_secret_rotate``, ``test_playbook_executors``,
#     ``test_metrics_and_cleanup_auth_n11_n12``, ``test_p_hard_1_fixes``,
#     ``test_production_readiness`` — need combinations of real Redis /
#     Postgres / mesh keys.
#
# Full ledger + owners: docs/dev/test-debt.md.
def pytest_collection_modifyitems(config, items):  # noqa: ARG001, ANN001
    import re as _re
    _integration_file_patterns = _re.compile(
        r"(?:^|/)tests/("
        r"test_phase\d+_"
        r"|test_roadmap_deliverables\.py"
        r"|test_demo_workspace_blocked_paths\.py"
        r"|test_mesh_auth\.py"
        r"|test_n16_n20_ssrf_hardening\.py"
        r"|test_api_key_auth\.py"
        r"|test_otel_pipeline\.py"
        r"|test_ei18_webhook_secret_rotate\.py"
        r"|test_playbook_executors\.py"
        r"|test_metrics_and_cleanup_auth_n11_n12\.py"
        r"|test_p_hard_1_fixes\.py"
        r"|test_production_readiness\.py"
        r"|test_mitre_coverage\.py"       # needs mesh keys + real gateway
        r"|test_board_report\.py"         # reads ui/src/pages/ExecutiveDashboard.jsx
        r"|test_sdk_integrations\.py"     # SDK source-string + JSX presence checks
        r")"
    )
    import pytest as _pytest
    integration_mark = _pytest.mark.integration
    for item in items:
        try:
            path = str(item.fspath)
        except Exception:
            continue
        if "/tests/integration/" in path or _integration_file_patterns.search(path):
            item.add_marker(integration_mark)
