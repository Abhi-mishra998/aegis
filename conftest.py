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
