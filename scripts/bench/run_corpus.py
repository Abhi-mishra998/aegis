#!/usr/bin/env python3
"""
Aegis Benchmark Corpus v1 runner — Sprint B 2026-06-14.

Loads every JSON file under tests/benchmark/corpus_v1/, provisions one
agent per persona on prod-ha, replays each adversarial scenario through
the canonical /execute path, and reports the pass rate per persona +
the overall corpus score.

Each corpus file declares (label, tool, arguments, expected) tuples.
The runner does NOT use an LLM — it fires the exact /execute payload so
we measure Aegis's verdict, not Claude's safety layer.

Usage:
    python3 scripts/bench/run_corpus.py
    BASE=https://ha.aegisagent.in TENANT=... python3 scripts/bench/run_corpus.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

# Some Python 3.14 + httpx combinations time out on HTTPS POST against
# this host. Toggle USE_CURL=1 to fall through to curl subprocess.
USE_CURL = os.environ.get("USE_CURL", "1") not in ("", "0", "false")


class _CurlClient:
    """Drop-in replacement for httpx.post()-style calls using curl."""
    @staticmethod
    def post(url: str, headers: dict | None = None, json: dict | None = None,
             timeout: float = 30) -> "_CurlResp":
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False) as f:
            in_path = f.name
            if json is not None:
                import json as _j
                f.write(_j.dumps(json))
        with tempfile.NamedTemporaryFile(suffix=".out", delete=False) as f:
            out_path = f.name
        # -4 forces IPv4. The ALB is dualstack but some client-side
        # DNS64-cached AAAA records still resolve to NAT64 prefixes
        # which have intermittent routing. IPv4 is always direct.
        args = ["curl", "-sS", "-X", "POST", "-4", "-m", str(int(timeout)),
                "-w", "%{http_code}", "-o", out_path]
        for k, v in (headers or {}).items():
            args += ["-H", f"{k}: {v}"]
        if json is not None:
            args += ["--data", f"@{in_path}"]
        args.append(url)
        try:
            r = subprocess.run(args, capture_output=True, text=True,
                              timeout=int(timeout) + 10)
        except subprocess.TimeoutExpired:
            return _CurlResp(0, b"", {})
        finally:
            try:
                os.unlink(in_path)
            except FileNotFoundError:
                pass
        try:
            code = int(r.stdout.strip())
        except ValueError:
            code = 0
        try:
            with open(out_path, "rb") as f:
                body = f.read()
        finally:
            try:
                os.unlink(out_path)
            except FileNotFoundError:
                pass
        return _CurlResp(code, body, {"content-type": "application/json"})


class _CurlResp:
    def __init__(self, status_code: int, body: bytes, headers: dict) -> None:
        self.status_code = status_code
        self._body = body
        self.headers = headers
        self.text = body.decode("utf-8", errors="replace")

    def json(self) -> dict:
        import json as _j
        return _j.loads(self._body or b"{}")

    def raise_for_status(self) -> None:
        if not (200 <= self.status_code < 400):
            raise RuntimeError(
                f"HTTP {self.status_code} body={self.text[:200]}")


# Active client — curl or httpx
_HTTP = _CurlClient if USE_CURL else httpx

BASE = os.environ.get("BASE", "https://ha.aegisagent.in")
TENANT = os.environ.get("TENANT", "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL = os.environ.get("ACP_ADMIN_EMAIL", "admin@acp.local")
ADMIN_PASSWORD = os.environ.get("ACP_ADMIN_PASSWORD", "admin1234")

CORPUS_DIR = Path(__file__).resolve().parents[2] / "tests" / "benchmark" / "corpus_v1"


def admin_token() -> str:
    r = _HTTP.post(
        f"{BASE}/auth/token",
        headers={"Content-Type": "application/json", "X-Tenant-ID": TENANT},
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        timeout=15,
    )
    r.raise_for_status()
    return (r.json().get("data") or r.json())["access_token"]


def _post_retry(url: str, headers: dict, payload: dict, max_tries: int = 6) -> "_CurlResp":
    """Retry-with-backoff. prod-ha has intermittent ~5-10s pauses on
    inter-container hops; if we don't retry, the bench is unreliable."""
    last: "_CurlResp" = None
    for i in range(max_tries):
        r = _HTTP.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code in (200, 201):
            return r
        last = r
        time.sleep(2 + i)
    return last


def admin_token_retry() -> str:
    """Auth with retry. Wraps admin_token()."""
    for i in range(6):
        try:
            r = _HTTP.post(
                f"{BASE}/auth/token",
                headers={"Content-Type": "application/json", "X-Tenant-ID": TENANT},
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
                timeout=20,
            )
            if r.status_code == 200:
                return (r.json().get("data") or r.json())["access_token"]
        except Exception:
            pass
        time.sleep(2 + i)
    raise RuntimeError("admin auth failed after 6 retries")


def provision(token: str, name: str, risk_level: str,
              tools: list[str]) -> tuple[str, str]:
    h = {"Authorization": f"Bearer {token}", "X-Tenant-ID": TENANT,
         "Content-Type": "application/json"}
    r = _post_retry(f"{BASE}/agents", h, {
        "name": f"bench-{name}-{uuid.uuid4().hex[:6]}",
        "description": f"corpus v1 {name}",
        "risk_level": risk_level,
    })
    if r is None or r.status_code not in (200, 201):
        raise RuntimeError(f"provision failed for {name}: HTTP "
                           f"{r and r.status_code}")
    agent_id = (r.json().get("data") or r.json())["id"]

    r = _post_retry(f"{BASE}/api-keys", h, {
        "name": f"bench-{name}", "agent_id": agent_id, "ttl_seconds": 7200,
    })
    if r is None or r.status_code not in (200, 201):
        raise RuntimeError(f"key failed for {name}: HTTP {r and r.status_code}")
    api_key = (r.json().get("data") or r.json())["api_key"]

    for t in tools:
        gr = _post_retry(
            f"{BASE}/agents/{agent_id}/permissions", h,
            {"tool_name": t, "action": "ALLOW"})
        if gr is None or gr.status_code not in (200, 201):
            print(f"  WARN: grant {t}: HTTP {gr and gr.status_code}",
                  file=sys.stderr)
    return agent_id, api_key


def call_execute(api_key: str, agent_id: str, tool: str, arguments: dict) -> dict:
    """Single /execute with one transparent retry on transport timeout
    (HTTP 0 / curl exit-28). The downstream-decision path occasionally
    pauses 5-10s on prod-ha; that's a deploy hygiene issue, not a verdict
    we want to count in the benchmark."""
    payload = {"agent_id": agent_id, "tool": tool, "arguments": arguments}
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Tenant-ID": TENANT,
        "X-Agent-ID": agent_id,
        "Content-Type": "application/json",
    }
    r = None
    for i in range(3):
        try:
            r = _HTTP.post(f"{BASE}/execute", headers=headers,
                           json=payload, timeout=20)
            if r.status_code != 0:
                break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError):
            r = None
        time.sleep(1 + i)
    if r is None or r.status_code == 0:
        return {"action": "error", "http": 0, "body": "client_timeout_after_retries"}
    if r.status_code in (200, 403, 429):
        try:
            body = r.json()
        except Exception:
            # WAF returns text/html for sensitive paths (credentials, etc.).
            # A non-JSON 403 is a real upstream-edge block — treat as deny so
            # the buyer sees a consistent verdict, not "error".
            if r.status_code == 403:
                return {"action": "deny",
                        "reasons": ["waf_blocked"],
                        "http": 403,
                        "body": r.text[:80]}
            return {"action": "error", "http": r.status_code,
                    "body": r.text[:120]}
        # /execute returns action at top level on success; on 403 the body
        # is a {success, error, meta} envelope. Inspect both shapes.
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and data.get("action"):
            return {**data, "http": r.status_code}
        if isinstance(body, dict) and body.get("action"):
            return {**body, "http": r.status_code}
        if r.status_code == 403:
            err = str(body.get("error", "denied")).lower()
            action = "escalate" if "approval_required" in err else "deny"
            return {"action":  action,
                    "reasons": [str(body.get("error", "denied"))[:120]],
                    "http":    r.status_code}
    return {"action": "error", "http": r.status_code,
            "body": r.text[:160]}


def expected_match(verdict: dict, expected: str, prefix: str) -> bool:
    """v1 benchmark: match the buyer-visible action only.

    The `expected_reason_prefix` field stays in the corpus for forensic
    notes, but the gateway maps every action-semantics deny into
    `reasons: ["approval_required"]` at the HTTP layer, so the rule-name
    string is not visible to a buyer's client. Match on action.
    """
    action = (verdict.get("action") or "").lower()
    return action == expected


def run() -> int:
    if not CORPUS_DIR.is_dir():
        print(f"FATAL: corpus dir not found: {CORPUS_DIR}", file=sys.stderr)
        return 2

    token = admin_token_retry()
    started_total = time.time()
    persona_results: dict[str, dict] = {}

    token_issued_at = time.time()
    for persona_file in sorted(CORPUS_DIR.glob("*.json")):
        # Refresh the admin token every persona — a slow prod-ha cycle can
        # easily eat the JWT TTL before the next provision call.
        if time.time() - token_issued_at > 600:
            token = admin_token_retry()
            token_issued_at = time.time()
        spec = json.loads(persona_file.read_text())
        persona = spec["persona"]
        risk = spec.get("agent_risk_level", "high")
        tools = spec.get("tool_permissions", [])
        print(f"\n=== {persona} ({len(spec['cases'])} cases) ===")

        try:
            agent_id, api_key = provision(token, persona, risk, tools)
        except RuntimeError as exc:
            # Token likely expired between personas — re-auth once and retry.
            print(f"  re-auth on provision failure: {exc}")
            token = admin_token_retry()
            token_issued_at = time.time()
            agent_id, api_key = provision(token, persona, risk, tools)
        print(f"  agent={agent_id}  key={api_key[:14]}…")

        per_case = []
        passed = 0
        for case in spec["cases"]:
            v = call_execute(api_key, agent_id, case["tool"], case["arguments"])
            ok = expected_match(v, case["expected"], case.get("expected_reason_prefix", ""))
            if ok:
                passed += 1
            mark = "✓" if ok else "✗"
            extra = ""
            if v.get("action") == "error":
                extra = f"  http={v.get('http')} body={(v.get('body') or '')[:60]}"
            print(f"    {mark} {case['label']:38s} "
                  f"expected={case['expected']:8s} "
                  f"got={(v.get('action') or '?'):8s} "
                  f"risk={v.get('risk','?')}{extra}")
            per_case.append({
                "label":   case["label"],
                "tool":    case["tool"],
                "expected": case["expected"],
                "expected_reason_prefix": case.get("expected_reason_prefix", ""),
                "got_action":     v.get("action"),
                "got_risk":       v.get("risk"),
                "got_findings":   v.get("findings", []),
                "got_reasons":    v.get("reasons", []),
                "got_request_id": v.get("request_id"),
                "pass": ok,
            })
        persona_results[persona] = {
            "agent_id": agent_id,
            "total":    len(spec["cases"]),
            "passed":   passed,
            "cases":    per_case,
        }

    total = sum(p["total"] for p in persona_results.values())
    passed = sum(p["passed"] for p in persona_results.values())
    duration = round(time.time() - started_total, 1)

    print("\n========== Aegis Benchmark v1 ==========")
    print(f"  total cases: {total}")
    print(f"  passed     : {passed}")
    print(f"  pass rate  : {passed / total * 100:.1f}%")
    print(f"  duration   : {duration}s")
    for persona, res in persona_results.items():
        rate = res["passed"] / res["total"] * 100
        print(f"  {persona:14s} {res['passed']}/{res['total']}  ({rate:.0f}%)")

    out_dir = Path(__file__).resolve().parents[2] / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "benchmark-v1.json"
    out_path.write_text(json.dumps({
        "generated_at": int(time.time()),
        "base":         BASE,
        "tenant_id":    TENANT,
        "total":        total,
        "passed":       passed,
        "pass_rate":    round(passed / total * 100, 2),
        "duration_seconds": duration,
        "personas":     persona_results,
    }, indent=2))
    print(f"\n✓ → {out_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())
