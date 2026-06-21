"""N16 + N20 — SSRF redirect bypass + XFF spoof on /demo/spawn-workspace.

N16: Outbound webhook MUST NOT follow HTTP redirects. The SSRF guard
(`_assert_safe_webhook_url`) only validates the INITIAL URL. Following
a 301/302 to http://127.0.0.1:8181 (OPA admin) or
http://169.254.169.254/... (cloud metadata) re-issues the request past
the SSRF check entirely.

N20: /demo/spawn-workspace's `_is_external_public` check on
X-Forwarded-For is necessary but not sufficient. An attacker inside the
EC2 fleet (RCE / SSM-exec / compromised CI) can spoof XFF: 8.8.8.8 and
share the 5-spawn budget across many spoofed-public IPs. The fix layers
in `_is_alb_hop(request.client.host)`: the immediate TCP peer MUST be
the ALB (a non-loopback, non-docker-bridge private IP).

These tests deliberately avoid importing the demo / webhook modules
directly — both pull settings from environment at import time, which
would require Postgres + Redis URLs configured for collection. We read
the source and either statically check it or exec just the helpers in
isolation. That keeps the test runnable from a clean checkout.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Make the project importable so source-file paths resolve from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

_WEBHOOK_EXECUTOR_PATH = _REPO_ROOT / "services" / "autonomy" / "webhook_executor.py"
_DEMO_ROUTER_PATH = _REPO_ROOT / "services" / "gateway" / "routers" / "demo.py"


# ─────────────────────────────────────────────────────────────────────────
# N16 — webhook executor must not follow redirects
# ─────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _redirect_transport(*, redirect_to: str, captured: list[httpx.Request]):
    """MockTransport that 301-redirects the first call, then 200s the second.

    Every request flows through `captured` so the test can assert how many
    hops the AsyncClient actually took.
    """
    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        if str(req.url) == redirect_to:
            # The "post-redirect" target would expose IMDS / OPA admin
            # in real life. The test asserts we never reach this branch.
            return httpx.Response(200, json={"leaked": True})
        return httpx.Response(301, headers={"Location": redirect_to})
    return httpx.MockTransport(handler)


class TestN16NoFollowRedirects:
    """Each outbound httpx.AsyncClient in webhook_executor.py constructs with
    follow_redirects=False (or the module-level _FOLLOW_REDIRECTS constant).
    The source-level assertion is the strongest test — runtime mocking with
    MockTransport bypasses the AsyncClient's redirect logic regardless of
    the flag, so source verification is the load-bearing test."""

    def _module_source(self) -> str:
        return _WEBHOOK_EXECUTOR_PATH.read_text()

    def test_module_constant_present_and_false(self):
        """The module-level _FOLLOW_REDIRECTS constant exists and is False.
        Source-level check so we don't have to import the module (which
        would require Postgres/Redis configured)."""
        src = self._module_source()
        assert "_FOLLOW_REDIRECTS = False" in src, (
            "webhook_executor.py must define `_FOLLOW_REDIRECTS = False` so "
            "every outbound AsyncClient refuses to follow redirects."
        )

    def test_every_async_client_passes_follow_redirects(self):
        src = self._module_source()
        # Every AsyncClient(...) constructor in webhook_executor.py must
        # carry either ``follow_redirects=_FOLLOW_REDIRECTS`` (the
        # constant) or the explicit literal ``follow_redirects=False``.
        # Scan with line-granular regex so a regression on any one site
        # surfaces with the offending line in the assertion message.
        bad_lines: list[str] = []
        for n, line in enumerate(src.splitlines(), start=1):
            if "httpx.AsyncClient(" not in line:
                continue
            if "_FOLLOW_REDIRECTS" in line:
                continue
            if "follow_redirects=False" in line:
                continue
            # Skip the comment block describing the rule
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            bad_lines.append(f"L{n}: {line}")
        assert not bad_lines, (
            "webhook_executor.py has httpx.AsyncClient sites missing the "
            "follow_redirects guard:\n" + "\n".join(bad_lines)
        )

    def test_runtime_redirect_returns_301_not_followed(self):
        """When AsyncClient is built with follow_redirects=False, a 301
        response is returned as-is — the redirect target is never fetched.
        Verify this is the actual httpx contract our fix relies on.
        """
        captured: list[httpx.Request] = []
        transport = _redirect_transport(
            redirect_to="http://169.254.169.254/latest/meta-data/",
            captured=captured,
        )
        async def _call():
            async with httpx.AsyncClient(
                transport=transport, follow_redirects=False, timeout=2.0,
            ) as c:
                return await c.post(
                    "https://attacker-controlled.example.com/hook",
                    json={"x": 1},
                )
        r = _run(_call())
        assert r.status_code == 301, (
            "follow_redirects=False must surface the 301 directly. "
            f"Got status={r.status_code} — httpx may have changed contract."
        )
        # Only the initial hop should have been issued — the second one
        # (the IMDS-style target) must NEVER appear.
        assert len(captured) == 1, (
            f"Expected exactly 1 outbound request (initial only), got "
            f"{len(captured)}: {[str(r.url) for r in captured]}"
        )
        assert "169.254" not in str(captured[0].url), (
            "Initial request reached the metadata endpoint — fix failed."
        )

    def test_runtime_redirect_DOES_follow_when_flag_true(self):
        """Negative control: without the fix httpx WOULD chase the redirect.
        Documents the bug class so a future reviewer sees why the flag matters.
        """
        captured: list[httpx.Request] = []
        transport = _redirect_transport(
            redirect_to="https://attacker-controlled.example.com/sink",
            captured=captured,
        )
        async def _call():
            async with httpx.AsyncClient(
                transport=transport, follow_redirects=True, timeout=2.0,
            ) as c:
                return await c.post(
                    "https://attacker-controlled.example.com/hook",
                    json={"x": 1},
                )
        r = _run(_call())
        assert r.status_code == 200
        assert len(captured) == 2, (
            "Expected initial + redirect (2 calls) when follow_redirects=True."
        )


# ─────────────────────────────────────────────────────────────────────────
# N20 — _is_alb_hop check on /demo/spawn-workspace
# ─────────────────────────────────────────────────────────────────────────

class TestN20IsAlbHop:
    """The spawn-workspace handler defines two local helpers inside its body:
    `_is_external_public` (the existing P2-1 check) and `_is_alb_hop` (this
    fix). The handler is decorated and depends on get_db / settings, so we
    pull the helpers out by source inspection + exec into a sandbox.
    """

    def _extract_helpers(self):
        """Recompile the handler's helper definitions in isolation so the
        tests don't need the FastAPI app, the DB, or Redis spun up.
        """
        src = _DEMO_ROUTER_PATH.read_text()
        # Find the block of helpers — they sit between the P2-1 comment
        # and the "client_host = request.client.host" line. Compile in a
        # fresh namespace with os + ipaddress + logger preloaded.
        start = src.find("import ipaddress as _ip\n    def _is_external_public")
        end = src.find("    client_host = request.client.host")
        assert start != -1 and end != -1 and end > start, (
            "couldn't locate helper block in demo.py — refactor likely"
        )
        block = src[start:end]
        # Strip the 4-space indent so it compiles at module level.
        dedented = "\n".join(
            line[4:] if line.startswith("    ") else line
            for line in block.splitlines()
        )
        ns: dict = {
            "os": os,
            "logger": MagicMock(),
        }
        exec(dedented, ns)
        return ns

    def test_alb_hop_accepts_rfc1918_10_8(self):
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        # The deployment's VPC is 10.20/16 — the production ALB sits inside
        # the private subnet. Treat 10.x.y.z as the canonical ALB IP shape.
        assert _is_alb_hop("10.20.3.5") is True
        assert _is_alb_hop("10.0.0.1") is True
        # Other RFC1918 ranges are also accepted (staging uses 10.30/16,
        # eu-west-1 uses 10.40/16, and the docker network in some compose
        # profiles falls inside 192.168/16).
        assert _is_alb_hop("192.168.10.5") is True

    def test_alb_hop_rejects_loopback(self):
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        # Loopback means the request came from inside the gateway container
        # itself — never the ALB.
        assert _is_alb_hop("127.0.0.1") is False
        assert _is_alb_hop("::1") is False

    def test_alb_hop_rejects_docker_bridge(self):
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        # 172.17/16 is the docker default bridge — every container-to-
        # container call on the default compose network surfaces here.
        assert _is_alb_hop("172.17.0.1") is False
        assert _is_alb_hop("172.17.5.42") is False
        assert _is_alb_hop("172.18.0.3") is False  # second bridge profile

    def test_alb_hop_rejects_link_local_and_imds(self):
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        # 169.254/16 is link-local; 169.254.169.254 specifically is IMDS.
        # An RCE that somehow plumbed a connection from IMDS into the
        # gateway should still be refused.
        assert _is_alb_hop("169.254.169.254") is False
        assert _is_alb_hop("169.254.1.1") is False

    def test_alb_hop_rejects_public(self):
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        # A public IP as the direct TCP peer would mean nginx + ALB are
        # bypassed — almost certainly a misconfig, definitely not an ALB.
        assert _is_alb_hop("8.8.8.8") is False
        assert _is_alb_hop("1.1.1.1") is False

    def test_alb_hop_rejects_empty_and_garbage(self):
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        assert _is_alb_hop("") is False
        assert _is_alb_hop(None) is False
        assert _is_alb_hop("not-an-ip") is False
        assert _is_alb_hop("999.999.999.999") is False

    def test_is_external_public_unchanged(self):
        """Sanity — the existing P2-1 check still rejects internal IPs and
        accepts a real public IP. Belt-and-suspenders: both checks must
        agree before /demo/spawn-workspace runs.
        """
        ns = self._extract_helpers()
        f = ns["_is_external_public"]
        assert f("8.8.8.8") is True
        assert f("1.1.1.1") is True
        assert f("127.0.0.1") is False
        assert f("10.20.3.5") is False
        assert f("172.17.0.1") is False
        assert f("169.254.169.254") is False

    def test_docker_bridge_cidrs_env_override(self, monkeypatch):
        """Operator can extend the deny-list via DEMO_DOCKER_BRIDGE_CIDRS
        without code change — verify the env hook is honoured.
        """
        monkeypatch.setenv(
            "DEMO_DOCKER_BRIDGE_CIDRS",
            "172.17.0.0/16,172.18.0.0/16,10.99.0.0/16",
        )
        ns = self._extract_helpers()
        _is_alb_hop = ns["_is_alb_hop"]
        # 10.99/16 — would normally be RFC1918 private (ALB-eligible), but
        # the operator listed it as a docker bridge, so refuse.
        assert _is_alb_hop("10.99.5.5") is False
        # 10.20/16 — not on the deny-list, stays ALB-eligible.
        assert _is_alb_hop("10.20.5.5") is True


class TestN20SpawnHandlerScenarios:
    """Sanity-check the documented attack scenarios via direct helper calls.

    The full /demo/spawn-workspace handler is exercised by integration
    tests with a live stack; here we verify the helper composition matches
    the table in the finding.
    """

    def _extract_helpers(self):
        # Same extractor as the other class; duplicated to keep tests
        # independently runnable.
        src = _DEMO_ROUTER_PATH.read_text()
        start = src.find("import ipaddress as _ip\n    def _is_external_public")
        end = src.find("    client_host = request.client.host")
        block = src[start:end]
        dedented = "\n".join(
            line[4:] if line.startswith("    ") else line
            for line in block.splitlines()
        )
        ns: dict = {"os": os, "logger": MagicMock()}
        exec(dedented, ns)
        return ns

    def test_loopback_peer_with_public_xff_rejected(self):
        """request.client.host=127.0.0.1 + XFF=8.8.8.8 — primary XFF check
        passes, secondary _is_alb_hop check rejects. This is the canonical
        N20 attack: an attacker inside the cluster spoofs XFF to defeat
        the per-public-IP rate limit and gets caught by the new guard.
        """
        ns = self._extract_helpers()
        is_public = ns["_is_external_public"]
        is_alb = ns["_is_alb_hop"]
        xff_first_hop = "8.8.8.8"
        peer = "127.0.0.1"
        assert is_public(xff_first_hop) is True, "primary check would allow"
        assert is_alb(peer) is False, (
            "secondary check must reject — peer is loopback, not the ALB"
        )

    def test_alb_peer_with_public_xff_allowed(self):
        """request.client.host=10.20.3.5 (ALB) + XFF=8.8.8.8 — both checks
        pass, request proceeds to Turnstile + rate-limit. This is the
        legitimate production path.
        """
        ns = self._extract_helpers()
        is_public = ns["_is_external_public"]
        is_alb = ns["_is_alb_hop"]
        assert is_public("8.8.8.8") is True
        assert is_alb("10.20.3.5") is True

    def test_docker_peer_with_public_xff_rejected(self):
        """request.client.host=172.17.0.5 (docker bridge) + XFF=8.8.8.8 —
        an attacker on the docker network spoofing XFF to look public.
        Same pattern, same rejection.
        """
        ns = self._extract_helpers()
        is_public = ns["_is_external_public"]
        is_alb = ns["_is_alb_hop"]
        assert is_public("8.8.8.8") is True
        assert is_alb("172.17.0.5") is False
