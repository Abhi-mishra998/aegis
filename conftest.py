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
