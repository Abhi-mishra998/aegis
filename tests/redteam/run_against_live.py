"""
FUP-3 / ARCH-7 2026-06-15 — Run-phase: drive the multi-LLM harness
against the LIVE Aegis deployment.

For every (backend, scenario) pair:
    1. provision a fresh agent in Aegis
    2. emit tool calls via the chosen backend (LLM or compromised replay)
    3. push each tool_use through /execute as that fresh agent
    4. score Aegis on what reached the gateway, NOT on whether the LLM
       chose to refuse — this is exactly the gap ARCH-7 was designed to
       close.

Usage:
    AEGIS_BASE=https://ha.aegisagent.in \
    ANTHROPIC_API_KEY=... \
    python tests/redteam/run_against_live.py compromised anthropic
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from tests.redteam.multi_llm_harness import SCENARIOS, emit_tool_calls


BASE   = os.environ.get("AEGIS_BASE",   "https://ha.aegisagent.in")
TENANT = os.environ.get("AEGIS_TENANT", "00000000-0000-0000-0000-000000000001")
ADMIN_EMAIL    = os.environ.get("AEGIS_ADMIN_EMAIL",    "admin@acp.local")
ADMIN_PASSWORD = os.environ.get("AEGIS_ADMIN_PASSWORD", "admin1234")


def _curl_post(url: str, headers: dict, body: dict | None = None, timeout: int = 25) -> tuple[int, dict]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        in_p = f.name
        if body is not None:
            f.write(json.dumps(body))
    with tempfile.NamedTemporaryFile(suffix=".out", delete=False) as f:
        out_p = f.name
    args = ["curl", "-sk", "-X", "POST", "-4",
            "-m", str(timeout), "-w", "%{http_code}", "-o", out_p]
    for k, v in headers.items():
        args += ["-H", f"{k}: {v}"]
    if body is not None:
        args += ["--data", f"@{in_p}"]
    args.append(url)
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout + 10)
    os.unlink(in_p)
    try:
        code = int(r.stdout.strip() or 0)
    except Exception:
        code = 0
    raw = open(out_p, "rb").read()
    os.unlink(out_p)
    try:
        data = json.loads(raw)
    except Exception:
        data = {"_raw": raw.decode("utf-8", "replace")[:200]}
    return code, data


def _admin_token() -> str:
    for _ in range(6):
        c, b = _curl_post(
            f"{BASE}/auth/token",
            {"Content-Type": "application/json", "X-Tenant-ID": TENANT},
            {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        )
        if c == 200 and "data" in b:
            return b["data"]["access_token"]
        time.sleep(2)
    raise SystemExit("admin auth failed")


def _provision(tok: str, label: str) -> str:
    h = {"Authorization": f"Bearer {tok}", "X-Tenant-ID": TENANT,
         "Content-Type": "application/json"}
    for _ in range(4):
        c, b = _curl_post(f"{BASE}/agents", h, {
            "name": f"redteam-arch7-{label}-{uuid.uuid4().hex[:6]}",
            "description": f"ARCH-7 live runphase {label}",
            "risk_level": "high",
        })
        if c == 201 and "data" in b:
            return b["data"]["id"]
        time.sleep(2)
    raise SystemExit(f"provision failed for {label}")


def _make_key(tok: str, agent_id: str) -> str:
    h = {"Authorization": f"Bearer {tok}", "X-Tenant-ID": TENANT,
         "Content-Type": "application/json"}
    c, b = _curl_post(f"{BASE}/api-keys", h, {
        "name": "arch7", "agent_id": agent_id, "ttl_seconds": 600,
    })
    if c in (200, 201) and "data" in b:
        return b["data"]["api_key"]
    raise SystemExit("api-key failed")


def _grant(tok: str, agent_id: str, tool: str) -> None:
    h = {"Authorization": f"Bearer {tok}", "X-Tenant-ID": TENANT,
         "Content-Type": "application/json"}
    _curl_post(f"{BASE}/agents/{agent_id}/permissions", h,
               {"tool_name": tool, "action": "ALLOW"})


def _execute(api_key: str, agent_id: str, tool: str, args: dict, session: str) -> tuple[int, dict]:
    h = {"Authorization": f"Bearer {api_key}", "X-Tenant-ID": TENANT,
         "X-Agent-ID": agent_id, "X-Session-ID": session,
         "Content-Type": "application/json"}
    return _curl_post(f"{BASE}/execute", h,
                      {"agent_id": agent_id, "tool": tool, "arguments": args})


def main(backends: list[str]) -> int:
    print(f"Aegis base: {BASE}")
    tok = _admin_token()
    print("admin auth ok")
    summary: dict[str, dict] = {}

    for backend in backends:
        print(f"\n=== Backend: {backend} ===")
        s_pass = s_fail = s_refused = 0
        for sc in SCENARIOS:
            scenario = dict(zip(
                ("label", "system_prompt", "user_prompt", "expected_tier", "must_tool"),
                sc,
            ))
            # Fresh agent so the cumulative scoring doesn't leak across.
            agent_id = _provision(tok, scenario["label"])
            for t in ("tool.sql_query", "tool.http_request", "tool.shell", "tool.read_file"):
                _grant(tok, agent_id, t)
            api_key = _make_key(tok, agent_id)
            time.sleep(2)  # api-key cache warm-up

            try:
                tool_calls = emit_tool_calls(backend, scenario) or []
            except Exception as e:
                tool_calls = []
                print(f"  [{scenario['label']}] emit error: {e}")

            if not tool_calls:
                print(f"  [{scenario['label']}] REFUSED-BY-MODEL (no tool calls)")
                s_refused += 1
                continue

            session = f"arch7-{backend}-{uuid.uuid4().hex[:8]}"
            tiers_seen: list[str] = []
            findings_seen: list[str] = []
            policy_ids: list[str] = []
            for tc in tool_calls:
                tool = "tool." + (tc.get("tool") or "unknown")
                args = tc.get("args") or {}
                code, body = _execute(api_key, agent_id, tool, args, session)
                action = body.get("action") or body.get("error", "")
                # Map response to tier
                if code == 403:
                    tier = ("escalate" if "approval" in str(action).lower()
                            else "deny")
                elif code == 200 and action == "allow":
                    tier = "allow"
                else:
                    tier = f"other:{code}"
                tiers_seen.append(tier)
                findings_seen.extend(body.get("findings") or [])
                pid = body.get("policy_id") or ""
                if pid:
                    policy_ids.append(pid)
                print(f"  [{scenario['label']}] {tool} → {tier} (HTTP {code}, "
                      f"policy={pid or '-'}, findings={(body.get('findings') or [])[:3]})")

            # Score: at LEAST ONE tool call must hit the expected tier.
            ok = scenario["expected_tier"] in tiers_seen
            if ok:
                print(f"    PASS — expected {scenario['expected_tier']} seen in {tiers_seen}")
                s_pass += 1
            else:
                print(f"    FAIL — expected {scenario['expected_tier']} but saw {tiers_seen}")
                s_fail += 1
        summary[backend] = {"pass": s_pass, "fail": s_fail, "refused": s_refused}

    print("\n\n=== ARCH-7 live run-phase summary ===")
    for b, c in summary.items():
        total = c["pass"] + c["fail"] + c["refused"]
        print(f"  {b:<14} pass={c['pass']:>2}/{total} "
              f"fail={c['fail']:>2}  refused-by-model={c['refused']:>2}")
    json.dump(summary, open("/tmp/arch7_runphase.json", "w"), indent=2)
    print("\n  Full summary at /tmp/arch7_runphase.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["compromised", "anthropic"]))
