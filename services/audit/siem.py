"""
ACP SIEM Integration
=====================
Forwards audit events to external SIEM platforms in real-time.

Supported targets:
  - Splunk HEC (HTTP Event Collector)
  - Datadog Logs API (HTTP)

Config:
  SIEM_TARGET: str = "" | "splunk" | "datadog"
  SPLUNK_HEC_URL: str — e.g. https://splunk.example.com:8088/services/collector
  SPLUNK_HEC_TOKEN: str
  DATADOG_LOGS_URL: str = "https://http-intake.logs.datadoghq.com/api/v2/logs"
  DATADOG_API_KEY: str

The forwarder is called from the audit writer after each successful DB write.
It is fire-and-forget (non-blocking); failures are counted in Prometheus but
never block the audit write path.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import httpx
import structlog

from sdk.common.config import settings

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
    """Canonical event shape forwarded to all SIEM targets."""

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

    @classmethod
    def from_audit_log(cls, row: AuditLog) -> SIEMEvent:
        """Build a SIEMEvent from an AuditLog ORM row."""
        ts = row.timestamp.isoformat() if row.timestamp else ""
        risk = float((row.metadata_json or {}).get("risk_score", 0.0))
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
        self._backend: SplunkHECForwarder | DatadogForwarder | None = None

        if target == "splunk":
            if not settings.SPLUNK_HEC_URL or not settings.SPLUNK_HEC_TOKEN:
                logger.warning(
                    "siem_splunk_misconfigured",
                    missing="SPLUNK_HEC_URL and/or SPLUNK_HEC_TOKEN not set",
                )
            else:
                self._backend = SplunkHECForwarder(
                    hec_url=settings.SPLUNK_HEC_URL,
                    hec_token=settings.SPLUNK_HEC_TOKEN,
                )
        elif target == "datadog":
            if not settings.DATADOG_API_KEY:
                logger.warning(
                    "siem_datadog_misconfigured",
                    missing="DATADOG_API_KEY not set",
                )
            else:
                self._backend = DatadogForwarder(
                    logs_url=settings.DATADOG_LOGS_URL,
                    api_key=settings.DATADOG_API_KEY,
                )
        elif target:
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
