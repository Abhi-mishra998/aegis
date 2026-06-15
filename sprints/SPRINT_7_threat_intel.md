# Sprint 7 — Threat-Intel Provider Layer

**Status:** in_progress
**Closes debt:** TD-5 — 9 exfil hosts + 6 offshore tokens + 22 destructive-shell
patterns baked into Python constants. No TTL, no SOC feedback loop, no
external feed. Detection ages out; new attacker domains slip past.
**Depends on:** Sprint 1 (Signal Registry — IOC matches feed the registered
signals).
**Blocks:** —

---

## Why this matters

Today the threat-intel surface is hardcoded in three files:

  * `services/policy/canonical.py:262` — `_KNOWN_EXFIL_DESTS` (transfer.sh,
    pastebin.com, gist.github.com, filebin.net, anonfiles.com, 0x0.st,
    ngrok.io, trycloudflare.com, discord.com/api/webhooks, webhook.site).
  * `services/policy/canonical.py:269` — `_OFFSHORE_TOKENS` (offshore,
    cayman, bvi, …).
  * `services/security/objectives/impact.py` — destructive shell pattern
    regexes (rm -rf /, dd if=/dev/zero, mkfs, kubectl drain, …).

That set is frozen at deploy time. When the SOC discovers a new exfil
host on Monday, they wait for a code release on Friday. There's no way
for tenant-A to add an IOC their internal red team found without it
leaking into tenant-B's policy. There's no feed integration with the
threat-intel platforms a buyer already pays for (Recorded Future,
CrowdStrike Falcon Intelligence, Mandiant Advantage).

EDR vendors expose this surface as **custom indicators**. CrowdStrike
calls them "Custom IOAs," SentinelOne calls them "Custom Rules,"
Microsoft Defender calls them "Custom Indicators." Sprint 7 ships the
same surface for Aegis.

## Goal

Operators (per-tenant) and global admins (cross-tenant) can:

  1. **Add an IOC** — POST one record with `(kind, value, severity, source)`.
  2. **List IOCs** — paginated, filterable by kind / severity / source.
  3. **Delete an IOC** — by id, with audit-logged actor.
  4. **Configure a feed** — name + URL + format + refresh interval.
  5. **Force-refresh** — pull a feed now.

Runtime evaluation (`canonical.py` + `objectives/impact.py`) consults
the cache before falling back to the hardcoded constants — so nothing
regresses if the cache is empty, and an operator-added IOC is honoured
within seconds of the POST.

## IOC kinds

  * `exfil_host` — substring match against the destination URL/host.
  * `c2_domain` — same shape; semantically "known C2 infrastructure."
  * `offshore_token` — substring match against the canonical's "offshore"
    blob detection.
  * `destructive_shell` — regex match against the shell command body.
  * `malicious_path` — substring match against file paths.
  * `privilege_token` — substring match against the privilege-escalation
    detection blob.

The kind is stored on the IOC record so callers can filter cheaply.

## Storage

Per-tenant Redis keyspace:

```
acp:ti:iocs:{tenant_id}:{kind}        SET   value (case-sensitive for regex, lowercase otherwise)
acp:ti:iocs_meta:{tenant_id}:{id}     HASH  {kind, value, severity, source, created_ts, actor}
acp:ti:iocs_index:{tenant_id}         SET   id  (for enumeration/delete by id)
acp:ti:feeds:{tenant_id}              HASH  field=name, value=JSON({url, format, refresh_seconds, last_pulled_ts})
acp:ti:last_refresh:{tenant_id}       STRING float
```

The "global" tenant_id (`"_global"`) is the cross-tenant overlay — IOCs
written there match every tenant. That's how the curated default list
ships into Aegis — see `providers.GlobalDefaultsProvider` which seeds
on first request and keeps the hardcoded constants alive as IOCs.

24 h TTL on the per-tenant sets so a broken feed doesn't permanently
poison policy. The feed config itself has no TTL — it's configured state.

## Provider framework

```python
class BaseProvider(abc.ABC):
    name: str
    async def collect(self, tenant_id: str) -> list[IOCRecord]: ...
```

Sprint 7 ships:

  * `StaticListProvider` — wraps a Python list (used to keep
    `_KNOWN_EXFIL_DESTS` etc. addressable as IOCs without forcing the
    operator to type each one).
  * `HttpFeedProvider` — pulls a URL on a schedule; parses
    `text/plain` (one IOC per line) or `application/json` (array of
    `{kind, value, severity}` objects). Bounded timeout + retries.

The orchestrator runs the configured providers hourly; failures are
logged but don't block other providers. Same shape as the Sprint 5
IAG ingestion.

## Runtime hook

`services/security/threatintel/runtime.py` exposes:

```python
async def match(redis, *, tenant_id, kind, value) -> bool: ...
async def match_any(redis, *, tenant_id, kind, candidates: Iterable[str]) -> bool: ...
async def matches_for_kind(redis, *, tenant_id, kind) -> set[str]: ...
```

The canonical evaluator calls `match_any(kind="exfil_host", candidates=[host, url])`
and OR-s the result with the hardcoded constant — backwards compatible.
On a Redis fault the runtime falls open to the hardcoded list so
detection never breaks.

## Success criteria

1. New module `services/security/threatintel/ioc.py` — `IOCRecord` +
   `KIND_*` constants. Pure types.
2. New module `services/security/threatintel/store.py` — Redis read/write
   for IOCs + feed config.
3. New module `services/security/threatintel/providers.py` — base +
   StaticList + HttpFeed.
4. New module `services/security/threatintel/runtime.py` — match /
   match_any / matches_for_kind helpers. Used by the canonical eval.
5. New router `services/gateway/routers/threatintel.py`:
     * `GET    /threat-intel/iocs` (filterable)
     * `POST   /threat-intel/iocs`
     * `DELETE /threat-intel/iocs/{id}`
     * `GET    /threat-intel/feeds`
     * `PUT    /threat-intel/feeds/{name}`
     * `POST   /threat-intel/refresh`
6. canonical.py: replace direct `_KNOWN_EXFIL_DESTS` lookup with
   `runtime.match_any(kind="exfil_host", …) or hardcoded fallback`.
   Same shape for `_OFFSHORE_TOKENS`.
7. Unit tests (target 14+ pass):
   * `test_threatintel_store_round_trip_set_get`
   * `test_threatintel_store_delete_by_id`
   * `test_threatintel_store_list_filters_by_kind`
   * `test_threatintel_runtime_match_hits_cache_first`
   * `test_threatintel_runtime_match_any_or_short_circuits`
   * `test_threatintel_runtime_match_redis_fault_returns_false`
   * `test_threatintel_provider_static_list_emits_records`
   * `test_threatintel_provider_http_feed_parses_text_lines`
   * `test_threatintel_provider_http_feed_parses_json_array`
   * `test_threatintel_provider_http_feed_handles_500_retry`
   * `test_threatintel_orchestrator_runs_each_provider`
   * `test_threatintel_orchestrator_single_provider_failure_does_not_block_others`
8. Live: deploy + verify
   `GET /threat-intel/iocs?kind=exfil_host` returns the curated default
   list (seeded by `GlobalDefaultsProvider`), `POST` adds a new IOC,
   the canonical eval picks it up on the next call.

## Non-goals

  * **Persistent IOC storage in Postgres.** Sprint 7 is Redis-only; cold
    storage lands in Sprint 8 if needed.
  * **Native adapters for CrowdStrike / Recorded Future / Mandiant.**
    The generic HttpFeedProvider works against their REST exports;
    vendor-specific OAuth flows are deferred.
  * **TI feed marketplace UI.** Operators configure via the router.
  * **STIX/TAXII** — same reason; the HTTP feed format is enough for
    Sprint 7.

## Files

**Added:**
  * `services/security/threatintel/__init__.py`
  * `services/security/threatintel/ioc.py`
  * `services/security/threatintel/store.py`
  * `services/security/threatintel/providers.py`
  * `services/security/threatintel/runtime.py`
  * `services/gateway/routers/threatintel.py`
  * `tests/security/test_threatintel_store.py`
  * `tests/security/test_threatintel_providers.py`
  * `tests/security/test_threatintel_runtime.py`

**Touched:**
  * `services/policy/canonical.py` — switch `_KNOWN_EXFIL_DESTS` /
    `_OFFSHORE_TOKENS` consumers to `runtime.match_any(...) or hardcoded`.
  * `services/gateway/main.py` — register router.
  * `services/gateway/middleware.py` — `/threat-intel` path exemption.

## Rollout + rollback

  * Deploy + restart `acp_gateway`.
  * Cache empty by default; behaviour matches today exactly.
  * If runtime starts misbehaving, set `ACP_THREAT_INTEL_ENABLED=0`
    and restart — canonical falls back to constants.
