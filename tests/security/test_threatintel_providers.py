"""Sprint 7 — Threat-Intel provider + orchestrator tests."""
from __future__ import annotations

import pytest

from services.security.threatintel import providers as ti_providers
from services.security.threatintel import store as ti_store
from services.security.threatintel.ioc import (
    KIND_EXFIL_HOST,
    KIND_OFFSHORE_TOKEN,
    SEV_HIGH,
    SOURCE_OPERATOR,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, k, v, ex=None, nx=False):
        self.kv[k] = str(v); return True
    async def get(self, k):
        v = self.kv.get(k); return v.encode() if isinstance(v, str) else v
    async def delete(self, k):
        n = 0
        for store_dict in (self.kv, self.sets, self.hashes):
            if k in store_dict:
                del store_dict[k]; n += 1
        return n
    async def expire(self, k, ex): return True
    async def sadd(self, k, *vals):
        s = self.sets.setdefault(k, set())
        before = len(s); s.update(str(v) for v in vals); return len(s) - before
    async def srem(self, k, *vals):
        s = self.sets.get(k, set())
        before = len(s)
        for v in vals: s.discard(str(v))
        return before - len(s)
    async def smembers(self, k):
        return {v.encode() for v in self.sets.get(k, set())}
    async def hset(self, k, field=None, value=None, mapping=None, **kw):
        h = self.hashes.setdefault(k, {})
        if mapping:
            for kk, vv in mapping.items():
                h[kk] = str(vv) if vv is not None else ""
        return 1
    async def hgetall(self, k):
        h = self.hashes.get(k, {})
        return {kk.encode(): vv.encode() for kk, vv in h.items()}


class _FakeResp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHttpx:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def get(self, url: str, *, timeout=None):
        self.calls.append(url)
        if not self._responses:
            raise RuntimeError("test exhausted scripted responses")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.mark.asyncio
async def test_static_list_provider_emits_one_record_per_value():
    p = ti_providers.StaticListProvider(
        name="t", tenant_id="t1", kind=KIND_EXFIL_HOST,
        values=["a.io", "b.io", "c.io"],
    )
    out = await p.collect()
    assert len(out) == 3
    assert {r.value for r in out} == {"a.io", "b.io", "c.io"}
    assert all(r.kind == KIND_EXFIL_HOST for r in out)
    assert all(r.tenant_id == "t1" for r in out)


@pytest.mark.asyncio
async def test_http_feed_provider_parses_text_one_per_line():
    h = _FakeHttpx([_FakeResp(200, "# header\nfoo.io\nbar.io\n\n# trailer\nbaz.io\n")])
    p = ti_providers.HttpFeedProvider(h, ti_providers.HttpFeedConfig(
        name="text-feed", tenant_id="t1", kind=KIND_EXFIL_HOST,
        url="https://x/iocs.txt", format="text",
    ))
    out = await p.collect()
    assert {r.value for r in out} == {"foo.io", "bar.io", "baz.io"}


@pytest.mark.asyncio
async def test_http_feed_provider_parses_json_array():
    body = '[{"value":"a.io"},{"value":"b.io"},"c.io"]'
    h = _FakeHttpx([_FakeResp(200, body)])
    p = ti_providers.HttpFeedProvider(h, ti_providers.HttpFeedConfig(
        name="json-feed", tenant_id="t1", kind=KIND_EXFIL_HOST,
        url="https://x/iocs.json", format="json",
    ))
    out = await p.collect()
    assert {r.value for r in out} == {"a.io", "b.io", "c.io"}


@pytest.mark.asyncio
async def test_http_feed_5xx_retries_then_succeeds():
    h = _FakeHttpx([_FakeResp(502), _FakeResp(503), _FakeResp(200, "ok.io\n")])
    p = ti_providers.HttpFeedProvider(h, ti_providers.HttpFeedConfig(
        name="retry", tenant_id="t1", kind=KIND_EXFIL_HOST,
        url="https://x/iocs.txt", format="text",
    ))
    out = await p.collect()
    assert [r.value for r in out] == ["ok.io"]
    assert len(h.calls) == 3


@pytest.mark.asyncio
async def test_http_feed_4xx_fails_fast_no_retry():
    h = _FakeHttpx([_FakeResp(404)])
    p = ti_providers.HttpFeedProvider(h, ti_providers.HttpFeedConfig(
        name="404", tenant_id="t1", kind=KIND_EXFIL_HOST,
        url="https://x/iocs.txt", format="text",
    ))
    out = await p.collect()
    assert out == []
    assert len(h.calls) == 1


@pytest.mark.asyncio
async def test_global_defaults_provider_seeds_curated_defaults():
    """The default exfil hosts + offshore tokens get written to GLOBAL."""
    r = _FakeRedis()
    providers = ti_providers.global_defaults_providers()
    summary = await ti_providers.run_providers(r, providers)
    assert all(v > 0 for v in summary.values())
    exfil = await ti_store.values_for_kind(
        r, tenant_id=ti_store.GLOBAL_TENANT_ID, kind=KIND_EXFIL_HOST,
    )
    assert "transfer.sh" in exfil
    offshore = await ti_store.values_for_kind(
        r, tenant_id=ti_store.GLOBAL_TENANT_ID, kind=KIND_OFFSHORE_TOKEN,
    )
    assert "cayman" in offshore


@pytest.mark.asyncio
async def test_orchestrator_single_failure_does_not_block_others():
    """One provider raising must not blank the rest."""
    class _ExplodingProvider(ti_providers.BaseProvider):
        name = "boom"
        async def collect(self):
            raise RuntimeError("kaboom")

    r = _FakeRedis()
    providers = [
        _ExplodingProvider(),
        ti_providers.StaticListProvider(
            name="t", tenant_id="t1", kind=KIND_EXFIL_HOST,
            values=["a.io"], severity=SEV_HIGH, source=SOURCE_OPERATOR,
        ),
    ]
    summary = await ti_providers.run_providers(r, providers)
    assert summary["boom"] == -1
    assert summary["t"] == 1
    vals = await ti_store.values_for_kind(r, tenant_id="t1", kind=KIND_EXFIL_HOST)
    assert vals == {"a.io"}


@pytest.mark.asyncio
async def test_orchestrator_stamps_last_refresh_per_tenant():
    r = _FakeRedis()
    providers = ti_providers.global_defaults_providers()
    await ti_providers.run_providers(r, providers)
    ts = await ti_store.get_last_refresh(r, tenant_id=ti_store.GLOBAL_TENANT_ID)
    assert ts > 0
