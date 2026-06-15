"""
ACP SIEM Integration
=====================
Forwards audit events to external SIEM platforms in real-time.

Supported targets (Sprint 2b extends this from 2 to 5):
  - ``splunk``    — Splunk HEC (HTTP Event Collector)
  - ``datadog``   — Datadog Logs API
  - ``elastic``   — Elastic Cloud Bulk Index API (Sprint 2b)
  - ``sentinel``  — Microsoft Sentinel HTTP Data Collector (Sprint 2b)
  - ``chronicle`` — Google Chronicle UDM Ingest API (Sprint 2b)

Config — common:
  SIEM_TARGET: str = "" | "splunk" | "datadog" | "elastic" | "sentinel" | "chronicle"
  SIEM_CRED_SOURCE: str = "env" (default) | "ssm"
  SIEM_SSM_PREFIX: str = "/aegis-siem"  # SSM Parameter Store prefix

Config — Splunk:
  SPLUNK_HEC_URL, SPLUNK_HEC_TOKEN

Config — Datadog:
  DATADOG_LOGS_URL, DATADOG_API_KEY

Config — Elastic:
  ELASTIC_CLOUD_ID, ELASTIC_API_KEY, ELASTIC_INDEX (default: aegis-audit)

Config — Sentinel:
  SENTINEL_WORKSPACE_ID, SENTINEL_SHARED_KEY, SENTINEL_LOG_TYPE

Config — Chronicle:
  CHRONICLE_CUSTOMER_ID, CHRONICLE_SERVICE_ACCOUNT_JSON, CHRONICLE_REGION

The forwarder is called from the audit writer after each successful DB write.
It is fire-and-forget (non-blocking); failures are counted in Prometheus but
never block the audit write path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

import os
from urllib.parse import urlencode

import httpx
import structlog

from sdk.common.config import settings

# A6 — Public AEVF base URL used in the back-reference field every SIEM
# record carries. Lets a buyer's SIEM consumer pivot from the row in
# their existing tooling to the verifiable AEVF bundle that contains
# the same row + the cryptographic chain.
AEVF_BASE_URL = os.environ.get("AEVF_PUBLIC_BASE_URL", "https://ha.aegisagent.in")
AEVF_SPEC_VERSION = "aevf/0.1.0"

if TYPE_CHECKING:
    from services.audit.models import AuditLog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus counters — imported lazily to avoid import cycles at test time
# ---------------------------------------------------------------------------

def _get_siem_counters():
    """Return (sent_counter, error_counter) Prometheus counters, or (None, None)."""
    try:
        from prometheus_client import Counter
        sent = Counter(
            "acp_siem_events_sent_total",
            "Total SIEM events successfully forwarded",
            ["target"],
        )
        errors = Counter(
            "acp_siem_forward_errors_total",
            "Total SIEM forwarding failures",
            ["target", "reason"],
        )
        return sent, errors
    except Exception:
        return None, None


_SIEM_SENT, _SIEM_ERRORS = _get_siem_counters()


# ---------------------------------------------------------------------------
# SIEMEvent dataclass
# ---------------------------------------------------------------------------


@dataclass
class SIEMEvent:
    """Canonical event shape forwarded to all SIEM targets.

    A6 addition: every event carries `aevf_bundle_url` + `aevf_spec_version` +
    `aevf_event_hash`. The buyer's SIEM consumer can pivot from a Splunk /
    Datadog / Sentinel / Chronicle / Elastic row directly to the
    cryptographically verifiable AEVF bundle for the same row's day —
    Aegis is the *evidence engine behind* the buyer's existing tooling,
    not a replacement.
    """

    timestamp: str          # ISO-8601
    tenant_id: str
    agent_id: str
    action: str
    tool: str | None
    decision: str
    reason: str | None
    risk_score: float
    request_id: str | None
    event_hash: str | None

    # A6 — AEVF back-reference fields. Always populated.
    aevf_bundle_url:   str | None = None
    aevf_spec_version: str = AEVF_SPEC_VERSION
    aevf_event_hash:   str | None = None      # alias of event_hash for SIEM clarity

    @classmethod
    def from_audit_log(cls, row: AuditLog) -> SIEMEvent:
        """Build a SIEMEvent from an AuditLog ORM row."""
        ts = row.timestamp.isoformat() if row.timestamp else ""
        risk = float((row.metadata_json or {}).get("risk_score", 0.0))

        # A6 — derive the day-bundle URL for the row's timestamp. A buyer's
        # auditor pulls https://<host>/compliance/export/eu-ai-act?period_start=<day>T00…&period_end=<day+1>T00…
        # and finds this row by event_hash. The bundle is the verifiable
        # one — Aegis only adds the URL pointer to the SIEM record.
        bundle_url: str | None = None
        if row.timestamp:
            day_start = row.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
            # Use timedelta for day+1 to handle month boundaries cleanly.
            from datetime import timedelta as _td
            day_end = day_start + _td(days=1)
            qs = urlencode({
                "period_start": day_start.isoformat().replace("+00:00", "Z"),
                "period_end":   day_end.isoformat().replace("+00:00", "Z"),
            })
            bundle_url = f"{AEVF_BASE_URL}/compliance/export/eu-ai-act?{qs}"

        return cls(
            timestamp=ts,
            tenant_id=str(row.tenant_id),
            agent_id=str(row.agent_id),
            action=row.action or "",
            tool=row.tool,
            decision=row.decision or "",
            reason=row.reason,
            risk_score=risk,
            request_id=row.request_id,
            event_hash=row.event_hash,
            aevf_bundle_url=   bundle_url,
            aevf_spec_version= AEVF_SPEC_VERSION,
            aevf_event_hash=   row.event_hash,
        )

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Splunk HEC forwarder
# ---------------------------------------------------------------------------


class SplunkHECForwarder:
    """Forwards events to a Splunk HTTP Event Collector endpoint."""

    def __init__(self, hec_url: str, hec_token: str) -> None:
        self._url = hec_url
        self._token = hec_token

    def _build_payload(self, event: SIEMEvent) -> dict:
        import time

        try:
            from datetime import datetime as _dt
            ts = _dt.fromisoformat(event.timestamp).timestamp() if event.timestamp else time.time()
        except (ValueError, OSError):
            ts = time.time()

        return {
            "time": ts,
            "host": "acp",
            "source": "acp:audit",
            "sourcetype": "acp:governance",
            "event": event.to_dict(),
        }

    async def forward(self, event: SIEMEvent) -> bool:
        """
        POST a single event to the Splunk HEC endpoint.

        Returns True on HTTP 2xx, False on any error. Never raises.
        """
        payload = self._build_payload(event)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    self._url,
                    json=payload,
                    headers={
                        "Authorization": f"Splunk {self._token}",
                        "Content-Type": "application/json",
                    },
                )
            if resp.is_success:
                if _SIEM_SENT:
                    _SIEM_SENT.labels(target="splunk").inc()
                return True
            logger.warning(
                "siem_splunk_http_error",
                status=resp.status_code,
                body=resp.text[:200],
            )
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="splunk", reason=f"http_{resp.status_code}").inc()
            return False
        except Exception as exc:
            logger.warning("siem_splunk_forward_failed", error=str(exc))
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="splunk", reason="exception").inc()
            return False


# ---------------------------------------------------------------------------
# Datadog Logs API forwarder
# ---------------------------------------------------------------------------


class DatadogForwarder:
    """Forwards events to the Datadog Logs API."""

    def __init__(self, logs_url: str, api_key: str) -> None:
        self._url = logs_url
        self._api_key = api_key

    def _build_payload(self, event: SIEMEvent) -> list[dict]:
        message = json.dumps(event.to_dict(), separators=(",", ":"))
        return [
            {
                "ddsource": "acp",
                "ddtags": f"tenant:{event.tenant_id},env:prod,decision:{event.decision}",
                "hostname": "acp-audit",
                "service": "acp-governance",
                "message": message,
            }
        ]

    async def forward(self, event: SIEMEvent) -> bool:
        """
        POST a single event to the Datadog Logs API.

        Returns True on HTTP 2xx (Datadog returns 202 Accepted), False on any error.
        Never raises.
        """
        payload = self._build_payload(event)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    self._url,
                    json=payload,
                    headers={
                        "DD-API-KEY": self._api_key,
                        "Content-Type": "application/json",
                    },
                )
            if resp.is_success:
                if _SIEM_SENT:
                    _SIEM_SENT.labels(target="datadog").inc()
                return True
            logger.warning(
                "siem_datadog_http_error",
                status=resp.status_code,
                body=resp.text[:200],
            )
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="datadog", reason=f"http_{resp.status_code}").inc()
            return False
        except Exception as exc:
            logger.warning("siem_datadog_forward_failed", error=str(exc))
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="datadog", reason="exception").inc()
            return False


# ---------------------------------------------------------------------------
# Elastic Cloud forwarder (Sprint 2b — closes audit C15 for Elastic)
# ---------------------------------------------------------------------------


class ElasticForwarder:
    """Forwards events to an Elastic Cloud cluster via the Bulk Index API.

    Wire protocol: ``POST {cluster_url}/_bulk`` with NDJSON pairs of
    ``{"index": {...}}\\n{event}\\n``. Auth is ``Authorization: ApiKey <b64>``.

    The cluster URL is decoded from the Elastic Cloud ID — the same value
    Elastic Cloud's UI shows under "Deployment → Cloud ID". The decoder is
    pure-Python (no elastic-py dependency) so the gateway image stays small.
    """

    def __init__(self, cloud_id: str, api_key: str, index: str = "aegis-audit") -> None:
        self._cloud_id = cloud_id
        self._api_key = api_key
        self._index = index
        self._cluster_url = self._decode_cluster_url(cloud_id)

    @staticmethod
    def _decode_cluster_url(cloud_id: str) -> str:
        """Cloud ID format: ``deployment-name:base64(host$es-uuid$kibana-uuid)``.
        We need the Elasticsearch host from the decoded part — ``host`` and
        ``es-uuid``. Returns ``https://{es-uuid}.{host}``."""
        try:
            _, encoded = cloud_id.split(":", 1)
        except ValueError as exc:
            raise ValueError(f"malformed Cloud ID (no colon): {cloud_id!r}") from exc
        import base64
        try:
            decoded = base64.b64decode(encoded + "==").decode("ascii", errors="strict")
        except Exception as exc:
            raise ValueError(f"Cloud ID payload not base64: {exc}") from exc
        parts = decoded.split("$")
        if len(parts) < 2:
            raise ValueError(f"Cloud ID payload missing host/uuid: {decoded!r}")
        host, es_uuid = parts[0], parts[1]
        return f"https://{es_uuid}.{host}"

    def _build_bulk_body(self, events: list[SIEMEvent]) -> str:
        """Return the NDJSON body for the Bulk API. One pair per event."""
        lines: list[str] = []
        for ev in events:
            lines.append(json.dumps({"index": {"_index": self._index}}, separators=(",", ":")))
            lines.append(json.dumps(ev.to_dict(), separators=(",", ":")))
        # Elastic requires a trailing newline after the last event.
        return "\n".join(lines) + "\n"

    async def forward(self, event: SIEMEvent) -> bool:
        return await self.batch_forward([event]) == 1

    async def batch_forward(self, events: list[SIEMEvent]) -> int:
        if not events:
            return 0
        body = self._build_bulk_body(events)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._cluster_url}/_bulk",
                    content=body.encode("utf-8"),
                    headers={
                        "Authorization": f"ApiKey {self._api_key}",
                        "Content-Type":  "application/x-ndjson",
                    },
                )
            if not resp.is_success:
                logger.warning(
                    "siem_elastic_http_error",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                if _SIEM_ERRORS:
                    _SIEM_ERRORS.labels(target="elastic", reason=f"http_{resp.status_code}").inc()
                return 0
            # Bulk API can succeed at HTTP level but fail per-document.
            # The ``errors`` field is True if any item failed.
            response_body = resp.json()
            items = response_body.get("items", []) or []
            sent = 0
            failures = 0
            for item in items:
                action = next(iter(item.values()), {})
                if 200 <= int(action.get("status", 500)) < 300:
                    sent += 1
                else:
                    failures += 1
            if _SIEM_SENT and sent:
                _SIEM_SENT.labels(target="elastic").inc(sent)
            if _SIEM_ERRORS and failures:
                _SIEM_ERRORS.labels(target="elastic", reason="item_failed").inc(failures)
            return sent
        except Exception as exc:
            logger.warning("siem_elastic_forward_failed", error=str(exc))
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="elastic", reason="exception").inc()
            return 0


# ---------------------------------------------------------------------------
# Microsoft Sentinel forwarder (Sprint 2b — closes audit C15 for Sentinel)
# ---------------------------------------------------------------------------


class SentinelForwarder:
    """Forwards events to Microsoft Sentinel via the Log Analytics HTTP
    Data Collector API.

    Wire protocol:
      POST ``https://{workspace_id}.ods.opinsights.azure.com/api/logs?api-version=2016-04-01``
      Headers: ``Log-Type``, ``x-ms-date``, ``Authorization: SharedKey {ws}:{sig}``
      Body:    JSON array of objects

    The signature is HMAC-SHA256 over a canonical string built from method,
    content length, content type, x-ms-date, and resource — base64-encoded.
    """

    _API_VERSION = "2016-04-01"

    def __init__(self, workspace_id: str, shared_key: str, log_type: str = "AegisAudit") -> None:
        if not workspace_id or not shared_key:
            raise ValueError("Sentinel workspace_id and shared_key are required")
        self._workspace_id = workspace_id
        self._shared_key = shared_key
        self._log_type = log_type
        self._url = (
            f"https://{workspace_id}.ods.opinsights.azure.com/api/logs"
            f"?api-version={self._API_VERSION}"
        )

    def _build_authorization(self, content_length: int, date_rfc1123: str) -> str:
        """HMAC-SHA256(shared_key_decoded, canonical_string), base64-encoded."""
        import base64
        import hashlib
        import hmac
        method = "POST"
        content_type = "application/json"
        resource = "/api/logs"
        canonical = (
            f"{method}\n{content_length}\n{content_type}\n"
            f"x-ms-date:{date_rfc1123}\n{resource}"
        )
        decoded_key = base64.b64decode(self._shared_key)
        signature = base64.b64encode(
            hmac.new(decoded_key, canonical.encode("utf-8"), hashlib.sha256).digest()
        ).decode("ascii")
        return f"SharedKey {self._workspace_id}:{signature}"

    async def forward(self, event: SIEMEvent) -> bool:
        return await self.batch_forward([event]) == 1

    async def batch_forward(self, events: list[SIEMEvent]) -> int:
        if not events:
            return 0
        import email.utils
        body = json.dumps([ev.to_dict() for ev in events], separators=(",", ":")).encode("utf-8")
        date_rfc1123 = email.utils.formatdate(usegmt=True)
        authorization = self._build_authorization(len(body), date_rfc1123)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    self._url,
                    content=body,
                    headers={
                        "Authorization":  authorization,
                        "Log-Type":       self._log_type,
                        "x-ms-date":      date_rfc1123,
                        "Content-Type":   "application/json",
                        "time-generated-field": "timestamp",
                    },
                )
            # Sentinel returns 200 with empty body on success.
            if resp.is_success:
                if _SIEM_SENT:
                    _SIEM_SENT.labels(target="sentinel").inc(len(events))
                return len(events)
            logger.warning(
                "siem_sentinel_http_error",
                status=resp.status_code,
                body=resp.text[:200],
            )
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="sentinel", reason=f"http_{resp.status_code}").inc()
            return 0
        except Exception as exc:
            logger.warning("siem_sentinel_forward_failed", error=str(exc))
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="sentinel", reason="exception").inc()
            return 0


# ---------------------------------------------------------------------------
# Google Chronicle forwarder (Sprint 2b — closes audit C15 for Chronicle)
# ---------------------------------------------------------------------------


class ChronicleForwarder:
    """Forwards events to Google Chronicle via the UDM Ingest API.

    Wire protocol:
      POST ``https://{region}-malachiteingestion-pa.googleapis.com/v2/udmevents:batchCreate``
      Auth: OAuth2 access token minted from a GCP service-account JWT.
      Body: ``{"customerId": "...", "events": [...]}`` in Unified Data Model.

    The service-account JSON contains an RSA private key; we sign a JWT
    locally (no ``google-auth`` dependency) and exchange it at
    ``https://oauth2.googleapis.com/token`` for an access token. The token
    is cached for its TTL (typically 3600 seconds) and refreshed on expiry.
    """

    _SCOPE = "https://www.googleapis.com/auth/chronicle-backstory"
    _TOKEN_URL = "https://oauth2.googleapis.com/token"
    _ENDPOINTS = {
        "us":               "https://malachiteingestion-pa.googleapis.com",
        "europe":           "https://europe-malachiteingestion-pa.googleapis.com",
        "asia-southeast1":  "https://asia-southeast1-malachiteingestion-pa.googleapis.com",
    }

    def __init__(
        self,
        service_account_json: str,
        customer_id: str,
        region: str = "us",
    ) -> None:
        if not service_account_json or not customer_id:
            raise ValueError("Chronicle service_account_json and customer_id are required")
        try:
            self._sa = json.loads(service_account_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Chronicle service_account_json is not valid JSON: {exc}") from exc
        required_keys = {"client_email", "private_key", "token_uri"}
        missing = required_keys - set(self._sa)
        if missing:
            raise ValueError(f"Chronicle service-account JSON missing keys: {sorted(missing)}")
        self._customer_id = customer_id
        self._region = region
        self._endpoint = self._ENDPOINTS.get(region, self._ENDPOINTS["us"])
        self._cached_token: tuple[str, float] | None = None  # (token, expires_at)

    def _mint_jwt(self) -> str:
        """Sign a Google-OAuth-compatible JWT with the service-account RSA key."""
        import base64
        import time as _time
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        now = int(_time.time())
        header = {"alg": "RS256", "typ": "JWT", "kid": self._sa.get("private_key_id", "")}
        claims = {
            "iss":   self._sa["client_email"],
            "scope": self._SCOPE,
            "aud":   self._sa.get("token_uri", self._TOKEN_URL),
            "iat":   now,
            "exp":   now + 3600,
        }

        def _b64url(data: bytes) -> str:
            return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

        header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        claims_b64 = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
        signing_input = f"{header_b64}.{claims_b64}".encode()

        priv = serialization.load_pem_private_key(
            self._sa["private_key"].encode("utf-8"), password=None,
        )
        signature = priv.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{header_b64}.{claims_b64}.{_b64url(signature)}"

    async def _get_access_token(self) -> str:
        import time as _time
        if self._cached_token and self._cached_token[1] > _time.time() + 60:
            return self._cached_token[0]
        assertion = self._mint_jwt()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                self._TOKEN_URL,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion":  assertion,
                },
            )
            resp.raise_for_status()
            body = resp.json()
        token = body["access_token"]
        ttl = int(body.get("expires_in", 3600))
        self._cached_token = (token, _time.time() + ttl)
        return token

    @staticmethod
    def _to_udm(event: SIEMEvent) -> dict[str, Any]:
        """Map a SIEMEvent to a minimal Chronicle UDM event.

        Full UDM is enormous; this map carries the fields Chronicle uses for
        timeline + search without forcing us to model every namespace.
        """
        d = event.to_dict()
        return {
            "metadata": {
                "event_timestamp": d["timestamp"],
                "event_type":      "GENERIC_EVENT",
                "vendor_name":     "Aegis",
                "product_name":    "Aegis Audit",
                "product_log_id":  d.get("request_id") or "",
            },
            "principal": {
                "user": {"userid": d.get("agent_id", "")},
            },
            "additional": d,
        }

    async def forward(self, event: SIEMEvent) -> bool:
        return await self.batch_forward([event]) == 1

    async def batch_forward(self, events: list[SIEMEvent]) -> int:
        if not events:
            return 0
        try:
            token = await self._get_access_token()
        except Exception as exc:
            logger.warning("siem_chronicle_token_failed", error=str(exc))
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="chronicle", reason="oauth_failed").inc()
            return 0
        body = {
            "customerId": self._customer_id,
            "events":     [self._to_udm(ev) for ev in events],
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{self._endpoint}/v2/udmevents:batchCreate",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            if resp.is_success:
                if _SIEM_SENT:
                    _SIEM_SENT.labels(target="chronicle").inc(len(events))
                return len(events)
            logger.warning(
                "siem_chronicle_http_error",
                status=resp.status_code,
                body=resp.text[:200],
            )
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="chronicle", reason=f"http_{resp.status_code}").inc()
            return 0
        except Exception as exc:
            logger.warning("siem_chronicle_forward_failed", error=str(exc))
            if _SIEM_ERRORS:
                _SIEM_ERRORS.labels(target="chronicle", reason="exception").inc()
            return 0


# ---------------------------------------------------------------------------
# Sprint 2b — SSM Parameter Store credential loader
# ---------------------------------------------------------------------------


def _load_ssm_credentials(prefix: str, target: str) -> dict[str, str]:
    """Fetch SIEM credentials from AWS SSM Parameter Store at boot.

    Reads every parameter under ``{prefix}/{target}/`` and returns
    ``{PARAM_NAME: value}`` with parameter names normalized to upper-snake.

    Boto3 errors are converted to a clear ``RuntimeError`` so a misconfigured
    operator sees the cause instead of a silent disabled-SIEM state. Boto3 is
    imported lazily so unit tests without AWS deps can still import this
    module.
    """
    try:
        import boto3  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            "SIEM_CRED_SOURCE=ssm requires boto3 to be installed"
        ) from exc

    path = f"{prefix.rstrip('/')}/{target}/"
    ssm = boto3.client("ssm")
    paginator = ssm.get_paginator("get_parameters_by_path")
    out: dict[str, str] = {}
    try:
        for page in paginator.paginate(Path=path, WithDecryption=True):
            for p in page.get("Parameters", []):
                # Strip the path prefix; keep the trailing component as the
                # UPPER_SNAKE key.
                name = p["Name"].split("/")[-1].upper()
                out[name] = p["Value"]
    except Exception as exc:
        raise RuntimeError(
            f"SIEM_CRED_SOURCE=ssm: could not list SSM parameters under {path!r}: {exc}"
        ) from exc
    return out


def _resolve_siem_credentials(target: str) -> dict[str, str]:
    """Resolve credentials for ``target`` from the configured source.

    Returns a dict the dispatcher feeds into the chosen forwarder. Keys
    are upper-snake field names matching the env-var names already in
    ``sdk/common/config.py`` (e.g. ``SPLUNK_HEC_URL``, ``ELASTIC_API_KEY``).
    """
    source = (settings.SIEM_CRED_SOURCE or "env").strip().lower()
    if source == "ssm":
        return _load_ssm_credentials(settings.SIEM_SSM_PREFIX, target)
    # Env-var lane (default): pluck the relevant fields off ``settings``.
    return {
        "SPLUNK_HEC_URL":                 settings.SPLUNK_HEC_URL,
        "SPLUNK_HEC_TOKEN":               settings.SPLUNK_HEC_TOKEN,
        "DATADOG_LOGS_URL":               settings.DATADOG_LOGS_URL,
        "DATADOG_API_KEY":                settings.DATADOG_API_KEY,
        "ELASTIC_CLOUD_ID":               settings.ELASTIC_CLOUD_ID,
        "ELASTIC_API_KEY":                settings.ELASTIC_API_KEY,
        "ELASTIC_INDEX":                  settings.ELASTIC_INDEX,
        "SENTINEL_WORKSPACE_ID":          settings.SENTINEL_WORKSPACE_ID,
        "SENTINEL_SHARED_KEY":            settings.SENTINEL_SHARED_KEY,
        "SENTINEL_LOG_TYPE":              settings.SENTINEL_LOG_TYPE,
        "CHRONICLE_CUSTOMER_ID":          settings.CHRONICLE_CUSTOMER_ID,
        "CHRONICLE_SERVICE_ACCOUNT_JSON": settings.CHRONICLE_SERVICE_ACCOUNT_JSON,
        "CHRONICLE_REGION":               settings.CHRONICLE_REGION,
    }


# ---------------------------------------------------------------------------
# Dispatcher / factory
# ---------------------------------------------------------------------------


class SIEMForwarder:
    """
    Factory + dispatcher for SIEM event forwarding.

    Reads SIEM_TARGET from settings on construction and instantiates the
    appropriate backend forwarder. If SIEM_TARGET is empty, all forward
    methods are no-ops.
    """

    def __init__(self) -> None:
        target = (settings.SIEM_TARGET or "").strip().lower()
        self._target = target
        self._backend: (
            SplunkHECForwarder | DatadogForwarder | ElasticForwarder
            | SentinelForwarder | ChronicleForwarder | None
        ) = None

        if not target:
            return

        creds = _resolve_siem_credentials(target)

        if target == "splunk":
            if not creds.get("SPLUNK_HEC_URL") or not creds.get("SPLUNK_HEC_TOKEN"):
                logger.warning(
                    "siem_splunk_misconfigured",
                    missing="SPLUNK_HEC_URL and/or SPLUNK_HEC_TOKEN not set",
                )
            else:
                self._backend = SplunkHECForwarder(
                    hec_url=creds["SPLUNK_HEC_URL"],
                    hec_token=creds["SPLUNK_HEC_TOKEN"],
                )
        elif target == "datadog":
            if not creds.get("DATADOG_API_KEY"):
                logger.warning(
                    "siem_datadog_misconfigured",
                    missing="DATADOG_API_KEY not set",
                )
            else:
                self._backend = DatadogForwarder(
                    logs_url=creds.get("DATADOG_LOGS_URL") or settings.DATADOG_LOGS_URL,
                    api_key=creds["DATADOG_API_KEY"],
                )
        elif target == "elastic":
            if not creds.get("ELASTIC_CLOUD_ID") or not creds.get("ELASTIC_API_KEY"):
                logger.warning(
                    "siem_elastic_misconfigured",
                    missing="ELASTIC_CLOUD_ID and/or ELASTIC_API_KEY not set",
                )
            else:
                self._backend = ElasticForwarder(
                    cloud_id=creds["ELASTIC_CLOUD_ID"],
                    api_key=creds["ELASTIC_API_KEY"],
                    index=creds.get("ELASTIC_INDEX") or "aegis-audit",
                )
        elif target == "sentinel":
            if not creds.get("SENTINEL_WORKSPACE_ID") or not creds.get("SENTINEL_SHARED_KEY"):
                logger.warning(
                    "siem_sentinel_misconfigured",
                    missing="SENTINEL_WORKSPACE_ID and/or SENTINEL_SHARED_KEY not set",
                )
            else:
                self._backend = SentinelForwarder(
                    workspace_id=creds["SENTINEL_WORKSPACE_ID"],
                    shared_key=creds["SENTINEL_SHARED_KEY"],
                    log_type=creds.get("SENTINEL_LOG_TYPE") or "AegisAudit",
                )
        elif target == "chronicle":
            if not creds.get("CHRONICLE_CUSTOMER_ID") or not creds.get("CHRONICLE_SERVICE_ACCOUNT_JSON"):
                logger.warning(
                    "siem_chronicle_misconfigured",
                    missing="CHRONICLE_CUSTOMER_ID and/or CHRONICLE_SERVICE_ACCOUNT_JSON not set",
                )
            else:
                self._backend = ChronicleForwarder(
                    service_account_json=creds["CHRONICLE_SERVICE_ACCOUNT_JSON"],
                    customer_id=creds["CHRONICLE_CUSTOMER_ID"],
                    region=creds.get("CHRONICLE_REGION") or "us",
                )
        else:
            logger.warning("siem_unknown_target", target=target)

    async def forward_audit_row(self, row: AuditLog) -> None:
        """
        Fire-and-forget forward of a single audit row.

        Converts the ORM row to a SIEMEvent and forwards it.
        All exceptions are swallowed — this must never block the audit write path.
        """
        if self._backend is None:
            return
        try:
            event = SIEMEvent.from_audit_log(row)
            await self._backend.forward(event)
        except Exception as exc:
            logger.warning("siem_dispatch_failed", error=str(exc))

    async def batch_forward(self, rows: list[AuditLog]) -> int:
        """
        Forward a batch of audit rows, returning the count of successful sends.

        Each row is forwarded independently so a single failure does not abort
        the rest of the batch.
        """
        if self._backend is None:
            return 0
        sent = 0
        for row in rows:
            try:
                event = SIEMEvent.from_audit_log(row)
                ok = await self._backend.forward(event)
                if ok:
                    sent += 1
            except Exception as exc:
                logger.warning("siem_batch_row_failed", error=str(exc))
        return sent


# ---------------------------------------------------------------------------
# Module-level singleton helper
# ---------------------------------------------------------------------------

_forwarder_instance: SIEMForwarder | None = None


def get_siem_forwarder() -> SIEMForwarder | None:
    """
    Return the singleton SIEMForwarder, or None if SIEM_TARGET is not configured.

    The forwarder is instantiated lazily on first call and cached for the process
    lifetime.
    """
    global _forwarder_instance  # noqa: PLW0603
    if _forwarder_instance is None:
        target = (settings.SIEM_TARGET or "").strip().lower()
        if not target:
            return None
        _forwarder_instance = SIEMForwarder()
    return _forwarder_instance


# ---------------------------------------------------------------------------
# Module-level forward helper (called by writer.py)
# ---------------------------------------------------------------------------


async def siem_forward(row: AuditLog) -> None:
    """
    Thin wrapper called via asyncio.create_task(safe_bg(siem_forward(row))).

    Calls the singleton forwarder; returns immediately if SIEM is disabled.
    """
    fwd = get_siem_forwarder()
    if fwd is None:
        return
    await fwd.forward_audit_row(row)
