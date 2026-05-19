#!/usr/bin/env python3
"""
ACP Demo Orchestrator — runs all three enterprise demo packs in sequence.

Packs:
  1. DB Copilot        — SQL governance (5 scenarios)
  2. DevOps Agent      — Kubernetes governance (9 scenarios)
  3. Support Agent     — CRM/PII governance (7 scenarios)

Usage:
    # Offline / dry-run (instant, no Groq calls, ~10s total)
    ACP_DRY_RUN=1 .venv/bin/python demos/run_all_demos.py

    # Live (real ACP gateway, real Groq calls, ~46s)
    .venv/bin/python demos/run_all_demos.py

Each pack is expected to:
  - Complete without raising an uncaught exception.
  - Finish in under 120 seconds.

A truth-only summary line is printed after each pack: every value comes
from the run itself, never hard-coded. If a pack fails, the line shows
FAIL with the actual exception message.
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import time
import traceback
from pathlib import Path

import httpx

# Allow imports from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_DIM    = "\033[2m"

DRY_RUN = os.getenv("ACP_DRY_RUN", "0") == "1"

_AUTONOMY_URL = os.getenv("ACP_AUTONOMY_URL", "http://localhost:8015")
_CREDS_FILES = [
    Path(__file__).parent / "devops_agent" / ".demo_creds.json",
    Path(__file__).parent / "support_agent" / ".demo_creds.json",
]

_PACKS = [
    ("AI Database Copilot",  "demos.db_copilot.scripted_demo"),
    ("AI DevOps Agent",      "demos.devops_agent.scripted_demo"),
    ("AI Support Agent",     "demos.support_agent.scripted_demo"),
]

_TIMEOUT_S = 120


async def _reset_autonomy_state() -> None:
    """Delete all autonomy contracts for demo agents so each pack starts clean."""
    if DRY_RUN:
        return
    for creds_file in _CREDS_FILES:
        if not creds_file.exists():
            continue
        try:
            import json as _json
            creds = _json.loads(creds_file.read_text())
            agent_id = creds.get("agent_id")
            if not agent_id:
                continue
            async with httpx.AsyncClient(timeout=5) as cl:
                resp = await cl.get(f"{_AUTONOMY_URL}/autonomy/contracts?agent_id={agent_id}")
                if resp.status_code != 200:
                    continue
                data = resp.json()
                items = (data.get("data") or data) if isinstance(data, dict) else data
                if isinstance(items, list):
                    for contract in items:
                        cid = contract.get("id") or contract.get("contract_id")
                        if cid:
                            await cl.delete(f"{_AUTONOMY_URL}/autonomy/contracts/{cid}")
        except Exception:
            pass  # best-effort; a stale contract is non-fatal


async def _run_pack(label: str, module_path: str) -> tuple[bool, float, str]:
    """
    Import and run the demo pack's main() coroutine.

    Returns (success, elapsed_s, error_message).
    """
    t0 = time.perf_counter()
    try:
        mod = importlib.import_module(module_path)
        coro = mod.main()
        await asyncio.wait_for(coro, timeout=_TIMEOUT_S)
        elapsed = time.perf_counter() - t0
        return True, elapsed, ""
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - t0
        return False, elapsed, f"timed out after {_TIMEOUT_S}s"
    except SystemExit as exc:
        elapsed = time.perf_counter() - t0
        # A sys.exit(0) from a demo is treated as success.
        if exc.code == 0 or exc.code is None:
            return True, elapsed, ""
        return False, elapsed, f"sys.exit({exc.code})"
    except Exception:
        elapsed = time.perf_counter() - t0
        return False, elapsed, traceback.format_exc(limit=5)


def _banner(title: str) -> None:
    bar = "═" * 62
    print(f"\n{_BOLD}{bar}{_RESET}")
    print(f"{_BOLD}  {title}{_RESET}")
    print(f"{_BOLD}{bar}{_RESET}\n")


async def main() -> None:
    mode_label = "DRY RUN (offline)" if DRY_RUN else "LIVE"
    _banner(f"ACP Enterprise Demo Suite — {mode_label}")

    results: list[tuple[str, bool, float, str]] = []

    for pack_label, module_path in _PACKS:
        print(f"{_BOLD}{'─'*62}{_RESET}")
        print(f"{_BOLD}  Pack: {pack_label}{_RESET}")
        print(f"{_BOLD}{'─'*62}{_RESET}\n")

        success, elapsed, err = await _run_pack(pack_label, module_path)
        results.append((pack_label, success, elapsed, err))

        if not success:
            print(f"\n{_RED}  ✗ {pack_label} FAILED ({elapsed:.1f}s){_RESET}")
            print(f"  Error: {err[:300]}")
        else:
            print(f"\n{_GREEN}  ✓ {pack_label} completed in {elapsed:.1f}s{_RESET}")

        # Reset autonomy contract state between packs so each pack starts fresh.
        await _reset_autonomy_state()
        await asyncio.sleep(0.5)

    # ── Summary table ──────────────────────────────────────────────────────────
    _banner("Demo Suite Summary")
    all_passed = True
    for label, success, elapsed, err in results:
        status = f"{_GREEN}PASS{_RESET}" if success else f"{_RED}FAIL{_RESET}"
        timing = f"~{elapsed:.0f}s"
        note   = "" if success else f"  ← {err.splitlines()[0][:60]}"
        print(f"  Pack: {label:<28}  {status}    {timing}{note}")
        if not success:
            all_passed = False

    print()
    if all_passed:
        print(f"  {_GREEN}{_BOLD}All scenarios passed. Demo platform ready.{_RESET}")
    else:
        print(f"  {_RED}{_BOLD}One or more packs failed — check output above.{_RESET}")
    print()

    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
