#!/usr/bin/env python3
"""
Sprint 9 prod-ha end-to-end QA harness.

A proper QA test — correct HTTP method per endpoint, correct body where
required, single source of truth for the expected outcome. No bash
array parsing bugs; no false negatives from sending GET to a POST-only
endpoint.

Run:

    python3 scripts/qa/test_prodha.py

Exit code is the number of failed tests (0 = all green).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass


@dataclass
class TestCase:
    name:     str
    method:   str
    path:     str
    body:     dict | None = None
    expected: int = 200
    auth:     bool = True
    # A test passes if status matches `expected` OR is in `also_ok`.
    # Used for endpoints where multiple 2xx codes are valid responses.
    also_ok:  tuple[int, ...] = ()


BASE = os.environ.get("AEGIS_BASE_URL", "https://ha.aegisagent.in").rstrip("/")
TENANT = os.environ.get(
    "AEGIS_TENANT_ID", "00000000-0000-0000-0000-000000000001"
)
EMAIL = os.environ.get("AEGIS_QA_EMAIL", "admin@acp.local")
PASSWORD = os.environ.get("AEGIS_QA_PASSWORD", "admin1234")
TIMEOUT = float(os.environ.get("AEGIS_QA_TIMEOUT", "12"))


def _request(method: str, path: str, *, body: dict | None = None,
             headers: dict | None = None, timeout: float = TIMEOUT) -> tuple[int, dict | str]:
    url = f"{BASE}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, str(exc)


def login() -> str:
    """Get a fresh admin JWT for the test run."""
    status, body = _request(
        "POST", "/auth/token",
        body={"email": EMAIL, "password": PASSWORD},
        headers={"X-Tenant-ID": TENANT},
    )
    if status != 200:
        raise SystemExit(
            f"FATAL: /auth/token returned {status}: {body!r}"
        )
    token = (body.get("data") or {}).get("access_token")
    if not token:
        raise SystemExit(f"FATAL: no access_token in /auth/token response: {body!r}")
    return token


def ensure_agent(token: str) -> str:
    """Return a real registered agent id. Create one if none exists, and
    grant the tool permissions the QA tests need so /execute returns
    200 (not 403). Without this, the policy correctly denies every
    request because the agent has no tools in its allow-list — which
    is the right security posture but makes the test depend on a
    `403 is also OK` cop-out."""
    auth = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   TENANT,
    }

    status, body = _request("GET", "/agents?limit=1", headers=auth)
    agent_id: str | None = None
    if status == 200:
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, list) and data:
            agent_id = data[0].get("id") or data[0].get("agent_id")
        elif isinstance(data, dict):
            items = data.get("items", []) or []
            if items:
                agent_id = items[0].get("id") or items[0].get("agent_id")

    if not agent_id:
        status, body = _request(
            "POST", "/agents",
            body={
                "name":        f"qa-{uuid.uuid4().hex[:8]}",
                "description": "QA harness agent (auto-created)",
                "status":      "active",
            },
            headers=auth,
        )
        if status not in (200, 201):
            raise SystemExit(f"FATAL: cannot create agent: {status} {body!r}")
        data = body.get("data") if isinstance(body, dict) else {}
        agent_id = data.get("id") or data.get("agent_id")

    # Grant the QA-relevant tools to the agent's allow-list. Idempotent at
    # the API level — duplicate (agent, tool_name) returns 409 or 200.
    for tool_name in ("tool.read_file",):
        status, body = _request(
            "POST", f"/agents/{agent_id}/permissions",
            body={
                "tool_name":  tool_name,
                "action":     "ALLOW",
                "granted_by": "qa-harness",
            },
            headers=auth,
        )
        if status not in (200, 201, 409):
            sys.stderr.write(
                f"  WARN: cannot grant {tool_name} to {agent_id}: "
                f"{status} {body!r}\n"
            )
    return agent_id


def build_tests(token: str, agent_id: str) -> list[TestCase]:
    """Every endpoint a client expects to work on prod-ha — with the
    actual HTTP method, body, and expected outcome."""
    return [
        # ── Auth ──────────────────────────────────────────────────────
        TestCase(
            "auth.token (admin login)",
            "POST", "/auth/token",
            body={"email": EMAIL, "password": PASSWORD},
            expected=200, auth=False,
        ),

        # ── System health ─────────────────────────────────────────────
        TestCase("system.health", "GET", "/system/health"),

        # ── Agents registry ───────────────────────────────────────────
        TestCase("agents.list", "GET", "/agents"),

        # ── Audit core ────────────────────────────────────────────────
        TestCase("audit.logs.list",     "GET", "/audit/logs?limit=3"),
        TestCase("audit.logs.summary",  "GET", "/audit/logs/summary"),

        # ── Sprint 4 — Fleet dashboard surface ────────────────────────
        TestCase("fleet.kpis",          "GET", "/audit/fleet/kpis?window_minutes=60"),
        TestCase("fleet.agent-health",  "GET",
                 "/audit/fleet/agent-health?window_minutes=60&rank_by=deny_rate"),
        TestCase("fleet.timeseries",    "GET",
                 "/audit/fleet/timeseries?metric=decisions&window_minutes=60&bucket_minutes=5"),
        TestCase("fleet.recent-events", "GET",
                 "/audit/fleet/recent-events?kind=any&limit=5"),
        TestCase("usage.burn-down",     "GET", "/usage/fleet/burn-down"),

        # ── Sprint 5 — Evaluation suite ───────────────────────────────
        TestCase("evaluation.datasets.list",   "GET",
                 "/audit/evaluation/datasets"),
        TestCase("evaluation.evaluators.list", "GET",
                 "/audit/evaluation/evaluators"),
        TestCase("evaluation.jobs.list",       "GET",
                 "/audit/evaluation/jobs"),
        TestCase("evaluation.efficacy.overview","GET",
                 "/audit/evaluation/efficacy/overview"),

        # ── Sprint 6 — Shadow Mode + online drift config ──────────────
        TestCase("shadow.policies.list",    "GET", "/audit/shadow/policies"),
        TestCase("shadow.online-eval.get",  "GET", "/audit/shadow/online-eval"),

        # ── Sprint 7 — Policy Playground (POST with valid body) ───────
        TestCase(
            "playground.validate",
            "POST", "/audit/playground/validate",
            body={
                "rules": [
                    {
                        "conditions": [
                            {"field": "tool", "operator": "eq", "value": "tool.shell"},
                        ],
                        "action":      "deny",
                        "description": "qa smoke rule",
                    },
                ],
                "policy_name": "qa_smoke",
            },
        ),

        # ── Identity graph (Sprint 1.5) ───────────────────────────────
        TestCase("graph.agents", "GET", "/graph/agents"),
        TestCase("graph.runtime-relationships", "GET",
                 "/graph/runtime-relationships?minutes=60"),

        # ── Flight Recorder (Sprint 3) ────────────────────────────────
        TestCase("flight.timelines", "GET", "/flight/timelines?minutes=60"),

        # ── API keys (Sprint 8 — what MCP / VS Code use) ──────────────
        TestCase("api-keys.list", "GET", "/api-keys"),

        # ── Transparency root (Sprint 1.3) ────────────────────────────
        TestCase("transparency.roots.list", "GET", "/transparency/roots"),

        # ── Decision pipeline end-to-end (the headline surface) ───────
        # /execute with a benign payload — the live pipeline MUST
        # return 200 (allow). Pre-flight already granted the agent the
        # tool.read_file permission so this is a real success
        # assertion, not "403 is also OK".
        TestCase(
            "execute.benign",
            "POST", "/execute",
            body={
                "tool":     "tool.read_file",
                "agent_id": agent_id,
                "payload":  {"raw": "README.md"},
            },
            expected=200,
        ),
    ]


def run(tests: list[TestCase], token: str) -> tuple[int, int, list[str]]:
    passes = 0
    fails = 0
    failures: list[str] = []
    headers_authed = {
        "Authorization": f"Bearer {token}",
        "X-Tenant-ID":   TENANT,
    }
    headers_unauthed = {"X-Tenant-ID": TENANT}

    for tc in tests:
        headers = headers_authed if tc.auth else headers_unauthed
        status, body = _request(tc.method, tc.path, body=tc.body, headers=headers)
        ok = (status == tc.expected) or (status in tc.also_ok)
        mark = "✓" if ok else "✗"
        sys.stdout.write(f"  {mark} {tc.name:<40} {tc.method:<5} {status}\n")
        sys.stdout.flush()
        if ok:
            passes += 1
        else:
            fails += 1
            preview = json.dumps(body) if isinstance(body, dict) else str(body)
            failures.append(
                f"{tc.name}: {tc.method} {tc.path} → {status} (expected {tc.expected}{'/' + ','.join(str(s) for s in tc.also_ok) if tc.also_ok else ''})\n"
                f"    body: {preview[:300]}"
            )
    return passes, fails, failures


def main() -> int:
    parser = argparse.ArgumentParser(prog="test_prodha.py")
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--retries", type=int, default=1,
                        help="Re-run failing tests this many times (for transient hiccups).")
    parser.add_argument("--retry-delay", type=float, default=2.0)
    args = parser.parse_args()
    globals()["BASE"] = args.base.rstrip("/")

    print(f"═══════════ Aegis prod-ha QA — {BASE} ═══════════")
    print(f"  tenant: {TENANT}")
    print(f"  email:  {EMAIL}")
    print()

    print("─ Pre-flight: login + ensure-agent ─")
    token = login()
    print(f"  ✓ login OK ({len(token)} char token)")
    agent_id = ensure_agent(token)
    print(f"  ✓ agent ready: {agent_id}")
    print()

    print("─ Tests ─")
    tests = build_tests(token, agent_id)
    total = len(tests)
    pass_count, fail_count, failures = run(tests, token)

    # Single retry pass for any transient failures.
    attempt = 1
    while failures and attempt <= args.retries:
        attempt += 1
        time.sleep(args.retry_delay)
        print(f"\n─ Retry {attempt - 1} on {len(failures)} failed test(s) ─")
        # Rebuild test list with same parameters to retry.
        failing_names = {f.split(":", 1)[0] for f in failures}
        retry_tests = [t for t in tests if t.name in failing_names]
        retry_pass, retry_fail, retry_failures = run(retry_tests, token)
        pass_count += retry_pass
        fail_count = fail_count - (retry_pass)
        failures = retry_failures

    print()
    print(f"═══════════ SUMMARY: {pass_count}/{total} pass, {len(failures)} fail ═══════════")
    if failures:
        print()
        print("─ Failures ─")
        for f in failures:
            print(f)
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
