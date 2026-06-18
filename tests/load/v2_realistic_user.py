"""Realistic-traffic Locust user for the v2.0 D1/D2 load tests.

Differs from `tests/load/soak_user.py` in one critical way: the mix here is
the **realistic SLA-proof mix** that the SPRINT.md §7 D1 spec calls for, not
the attack-shaped 60/15/10/10/5 mix of the soak harness. The two harnesses
answer different questions:

  - soak_user.py     -> "does the chain stay intact when we shove malformed,
                        oversized, and forged traffic at it for an hour?"
  - v2_realistic.py  -> "what is the customer-facing p50/p95/p99 latency and
                        error rate under realistic production-shape load?"

Traffic mix (SPRINT.md §7 D1):

    60  tool-execute   POST /execute (legit tool calls, valid auth)
    15  policy lifecycle  PUT /policies/{id} (upload) + POST /execute
                          (decision against the just-uploaded policy)
    10  audit queries   GET /logs?limit=50&after=<cursor>
    10  SSE subscribers GET /events/stream (long-lived per user)
     5  admin           GET /agents (list) + GET /status

Tenant + token resolution is identical to soak_user.py — orchestrator
writes SOAK_MANIFEST, each user picks one tenant on_start and stays bound.

Run via locust CLI:

    locust -f tests/load/v2_realistic_user.py --headless \\
        -u 1000 -r 50 -t 30m --host https://ha.aegisagent.in \\
        --csv reports/load-test-2026-Q3/1k-rps/locust

For the SPRINT D2 burst profile (10k VUs over 60s ramp + 5m hold), use the
locust step-load shape in v2_realistic_burst.py — same user, different
shape.
"""
from __future__ import annotations

import json
import os
import random
import secrets
import uuid
from typing import Any

from locust import HttpUser, between, events, task

# --------------------------------------------------------------------------- #
# Tenant manifest (shared shape with soak_user.py)                            #
# --------------------------------------------------------------------------- #

_MANIFEST_ENV = "SOAK_MANIFEST"


def _load_manifest() -> list[dict[str, str]]:
    path = os.environ.get(_MANIFEST_ENV)
    if not path or not os.path.isfile(path):
        return []
    try:
        return json.loads(open(path).read())
    except Exception:
        return []


_MANIFEST: list[dict[str, str]] = _load_manifest()


@events.test_start.add_listener
def _on_start(environment, **_):  # pragma: no cover — invoked by locust
    if not _MANIFEST:
        environment.runner.quit()
        raise RuntimeError(
            "v2_realistic_user.py: empty tenant manifest. Set SOAK_MANIFEST to a"
            " JSON file produced by tests/load/soak.py before launching locust."
        )


# --------------------------------------------------------------------------- #
# Tool surface used by the realistic mix                                      #
# --------------------------------------------------------------------------- #

# Tools allow-listed in every test tenant by the orchestrator. The realistic
# mix only calls tools that exist as agent permissions; otherwise local_eval
# 403s before policy ever runs and we measure auth, not policy throughput.
_TOOLS_LEGIT = ("read_file", "list_files", "http_get", "sql_select")
_POLICY_DOC = {
    "name": "v2-load-test-policy",
    "version": "1.0.0",
    "rules": [
        {"action": "allow", "match": {"tool": "read_file"}},
        {"action": "monitor", "match": {"tool": "http_get"}},
    ],
}


# --------------------------------------------------------------------------- #
# User                                                                        #
# --------------------------------------------------------------------------- #


class V2RealisticUser(HttpUser):
    """Realistic-traffic user. Mix weights match SPRINT.md §7 D1."""

    wait_time = between(0.2, 0.8)

    def on_start(self) -> None:
        idx = random.randint(0, len(_MANIFEST) - 1)
        entry = _MANIFEST[idx]
        self.tenant_id = entry["tenant_id"]
        self.token = entry["token"]
        self.client.headers.update({
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        })
        # Per-user policy id so the 15% policy-lifecycle task doesn't
        # serialise on a single global policy row.
        self.policy_id = f"v2-load-{uuid.uuid4().hex[:8]}"
        # Per-user audit cursor so successive queries paginate forward
        # rather than re-reading the same 50 rows.
        self.audit_cursor: str | None = None

    def _name(self, endpoint: str) -> str:
        # Per-tenant naming so locust CSV splits stats by tenant.
        return f"{endpoint}|tenant={self.tenant_id}"

    # ------------------------------------------------------------------ #
    # Weight 60 — tool-execute legit                                     #
    # ------------------------------------------------------------------ #

    @task(60)
    def tool_execute_legit(self) -> None:
        tool = random.choice(_TOOLS_LEGIT)
        body = {
            "tool": tool,
            "arguments": _benign_args_for(tool),
            "request_id": str(uuid.uuid4()),
        }
        self.client.post(
            f"/execute/{tool}",
            json=body,
            name=self._name("/execute/legit"),
        )

    # ------------------------------------------------------------------ #
    # Weight 15 — policy upload + decision                               #
    # ------------------------------------------------------------------ #

    @task(15)
    def policy_lifecycle(self) -> None:
        # PUT (or POST) the policy bundle, then execute against it.
        self.client.put(
            f"/policies/{self.policy_id}",
            json=_POLICY_DOC,
            name=self._name("/policies/PUT"),
        )
        self.client.post(
            "/execute/read_file",
            json={
                "tool": "read_file",
                "arguments": {"path": "/var/log/app.log"},
                "request_id": str(uuid.uuid4()),
            },
            name=self._name("/execute/after-policy"),
        )

    # ------------------------------------------------------------------ #
    # Weight 10 — audit log queries                                      #
    # ------------------------------------------------------------------ #

    @task(10)
    def audit_query(self) -> None:
        params: dict[str, str] = {"limit": "50", "action": "execute_tool"}
        if self.audit_cursor:
            params["after"] = self.audit_cursor
        resp = self.client.get(
            "/logs",
            params=params,
            name=self._name("/logs"),
        )
        try:
            payload = resp.json()
            rows = payload.get("data") or payload.get("rows") or []
            if rows:
                # Use the last row's id as the next cursor.
                self.audit_cursor = str(rows[-1].get("id", "") or "")
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Weight 10 — SSE subscribers (short-lived stream open per task fire)#
    # ------------------------------------------------------------------ #

    @task(10)
    def sse_subscribe(self) -> None:
        # Locust HttpUser isn't streaming-friendly, so we open the stream
        # with a small read timeout and count the first event observed.
        # This still exercises the gateway's SSE dispatcher path under load
        # without blocking the user task for the entire test duration.
        with self.client.get(
            "/events/stream",
            stream=True,
            name=self._name("/events/stream"),
            catch_response=True,
            timeout=2.5,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"sse status={resp.status_code}")
                return
            try:
                for chunk in resp.iter_content(chunk_size=64):
                    if chunk:
                        resp.success()
                        break
            except Exception as exc:
                resp.failure(f"sse stream error: {exc}")

    # ------------------------------------------------------------------ #
    # Weight 5 — admin endpoints                                         #
    # ------------------------------------------------------------------ #

    @task(5)
    def admin_endpoints(self) -> None:
        # /agents and /status alternate so we exercise both the tenant-scoped
        # list and the public status surface under sustained load.
        if random.random() < 0.5:
            self.client.get("/agents", name=self._name("/agents"))
        else:
            self.client.get("/status", name=self._name("/status"))


# --------------------------------------------------------------------------- #
# Argument fixtures                                                            #
# --------------------------------------------------------------------------- #


def _benign_args_for(tool: str) -> dict[str, Any]:
    """Build benign arguments for the legit-execute task that pass the
    canonical extractor + signal registry without firing any of the
    deny/escalate rules. We want the policy engine to run end-to-end
    on the *allow* path so we measure the customer-facing latency, not
    the deny short-circuit.
    """
    if tool == "read_file":
        return {"path": f"/var/log/app/{secrets.token_hex(4)}.log"}
    if tool == "list_files":
        return {"path": "/var/log/app"}
    if tool == "http_get":
        return {"url": "https://api.example.com/widgets"}
    if tool == "sql_select":
        return {"query": "SELECT id FROM events WHERE id = 1 LIMIT 10"}
    return {}
