"""
Sprint 2b — unit tests for the Elastic/Sentinel/Chronicle SIEM forwarders
(closes audit C15) plus the SSM credential-loader path.

These tests assert the wire protocol each vendor documents:

  * Elastic Bulk Index API — NDJSON body, ``Authorization: ApiKey ...``,
    cluster URL derived from the Elastic Cloud ID.
  * Sentinel HTTP Data Collector — HMAC-SHA256 signature, ``Log-Type``
    header, ``x-ms-date`` in RFC 1123.
  * Chronicle UDM Ingest — RS256 JWT minted from the service-account key,
    exchanged at the OAuth2 token endpoint, UDM payload posted with a
    bearer token.

The companion ``tests/integration/test_siem_endpoints.py`` exercises each
forwarder against a real SIEM instance when credentials are present in
SSM; both surfaces share the same code path.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.audit.siem import (
    ChronicleForwarder,
    ElasticForwarder,
    SentinelForwarder,
    SIEMEvent,
    _load_ssm_credentials,
    _resolve_siem_credentials,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(**overrides: Any) -> SIEMEvent:
    defaults = dict(
        timestamp="2026-06-13T00:00:00+00:00",
        tenant_id="t-1",
        agent_id="a-1",
        action="execute_tool",
        tool="db.query",
        decision="allow",
        reason=None,
        risk_score=0.1,
        request_id="req-1",
        event_hash="abc",
    )
    defaults.update(overrides)
    return SIEMEvent(**defaults)  # type: ignore[arg-type]


class _MockResponse:
    def __init__(self, *, status_code: int = 200, json_data: Any = None, text: str = "") -> None:
        self.status_code = status_code
        self._json = json_data
        self.text = text or ""

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise RuntimeError(f"HTTP {self.status_code}")


def _mock_async_client(post_response: _MockResponse):
    """Build an AsyncMock httpx.AsyncClient with a single post() stub."""
    client = AsyncMock()
    client.post = AsyncMock(return_value=post_response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, client


# ---------------------------------------------------------------------------
# Elastic
# ---------------------------------------------------------------------------


def _make_elastic_cloud_id() -> str:
    """Cloud ID format: ``name:base64(host$es-uuid$kibana-uuid)``."""
    payload = "cloud.example.com$es-uuid-1234$kibana-uuid-5678"
    return f"my-deployment:{base64.b64encode(payload.encode()).decode()}"


def test_elastic_decodes_cluster_url_from_cloud_id():
    forwarder = ElasticForwarder(
        cloud_id=_make_elastic_cloud_id(),
        api_key="QVBJX0tFWQ==",
    )
    assert forwarder._cluster_url == "https://es-uuid-1234.cloud.example.com"


def test_elastic_rejects_malformed_cloud_id():
    with pytest.raises(ValueError, match="malformed Cloud ID"):
        ElasticForwarder(cloud_id="no-colon-no-encoding", api_key="x")


@pytest.mark.asyncio
async def test_elastic_bulk_body_shape_and_auth():
    forwarder = ElasticForwarder(
        cloud_id=_make_elastic_cloud_id(),
        api_key="QVBJX0tFWQ==",
        index="aegis-audit",
    )
    response = _MockResponse(
        status_code=200,
        json_data={"errors": False, "items": [{"index": {"status": 201}}]},
    )
    cm, client = _mock_async_client(response)

    with patch("httpx.AsyncClient", return_value=cm):
        sent = await forwarder.batch_forward([_event(request_id="req-7")])

    assert sent == 1
    client.post.assert_awaited_once()
    call = client.post.await_args
    url = call.args[0] if call.args else call.kwargs["url"]
    headers = call.kwargs["headers"]
    body = call.kwargs.get("content", b"").decode("utf-8")

    assert url.endswith("/_bulk")
    assert headers["Authorization"] == "ApiKey QVBJX0tFWQ=="
    assert headers["Content-Type"] == "application/x-ndjson"
    # NDJSON: action line then doc line then trailing newline.
    lines = body.split("\n")
    assert len(lines) == 3 and lines[-1] == ""
    action = json.loads(lines[0])
    doc = json.loads(lines[1])
    assert action == {"index": {"_index": "aegis-audit"}}
    assert doc["request_id"] == "req-7"
    assert doc["tenant_id"] == "t-1"


@pytest.mark.asyncio
async def test_elastic_reports_item_failures_separately():
    forwarder = ElasticForwarder(
        cloud_id=_make_elastic_cloud_id(),
        api_key="x",
    )
    response = _MockResponse(
        status_code=200,
        json_data={"errors": True, "items": [
            {"index": {"status": 201}},
            {"index": {"status": 429, "error": {"type": "rate_limit"}}},
        ]},
    )
    cm, _ = _mock_async_client(response)
    with patch("httpx.AsyncClient", return_value=cm):
        sent = await forwarder.batch_forward([_event(request_id=f"r{i}") for i in range(2)])
    assert sent == 1   # one succeeded, one failed at the item level


# ---------------------------------------------------------------------------
# Sentinel
# ---------------------------------------------------------------------------


def _make_sentinel_shared_key() -> str:
    """Sentinel shared key is base64 of 32+ random bytes."""
    return base64.b64encode(b"\x00" * 32).decode("ascii")


def test_sentinel_requires_workspace_and_key():
    with pytest.raises(ValueError):
        SentinelForwarder(workspace_id="", shared_key="x")
    with pytest.raises(ValueError):
        SentinelForwarder(workspace_id="ws", shared_key="")


@pytest.mark.asyncio
async def test_sentinel_signature_and_headers():
    forwarder = SentinelForwarder(
        workspace_id="00000000-0000-0000-0000-000000000001",
        shared_key=_make_sentinel_shared_key(),
        log_type="AegisAudit",
    )
    response = _MockResponse(status_code=200, text="")
    cm, client = _mock_async_client(response)

    with patch("httpx.AsyncClient", return_value=cm):
        sent = await forwarder.batch_forward([_event()])

    assert sent == 1
    call = client.post.await_args
    headers = call.kwargs["headers"]
    body = call.kwargs["content"]
    assert "x-ms-date" in headers
    assert headers["Log-Type"] == "AegisAudit"
    assert headers["Authorization"].startswith(
        "SharedKey 00000000-0000-0000-0000-000000000001:"
    )
    # The signature segment is base64 — non-empty and decodable.
    sig_b64 = headers["Authorization"].split(":", 1)[1]
    decoded = base64.b64decode(sig_b64)
    assert len(decoded) == 32   # SHA-256 digest


@pytest.mark.asyncio
async def test_sentinel_signature_changes_with_body_length():
    """Two posts with different payload sizes must produce different signatures
    — proves the content-length is bound to the canonical signing string."""
    forwarder = SentinelForwarder(
        workspace_id="ws-1", shared_key=_make_sentinel_shared_key(),
    )
    sigs: list[str] = []
    for size in (1, 5):
        response = _MockResponse(status_code=200, text="")
        cm, client = _mock_async_client(response)
        with patch("httpx.AsyncClient", return_value=cm):
            await forwarder.batch_forward([_event() for _ in range(size)])
        sigs.append(client.post.await_args.kwargs["headers"]["Authorization"])
    assert sigs[0] != sigs[1]


# ---------------------------------------------------------------------------
# Chronicle
# ---------------------------------------------------------------------------


def _make_chronicle_service_account() -> str:
    """Generate an in-process RSA keypair and embed in a minimal SA JSON."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return json.dumps({
        "type":              "service_account",
        "client_email":      "aegis-test@aegis-test.iam.gserviceaccount.com",
        "private_key":       pem,
        "private_key_id":    "key-1",
        "token_uri":         "https://oauth2.googleapis.com/token",
        "project_id":        "aegis-test",
        "client_id":         "111111111111111111111",
    })


def test_chronicle_validates_service_account_json():
    with pytest.raises(ValueError, match="not valid JSON"):
        ChronicleForwarder(service_account_json="{not json", customer_id="x")
    with pytest.raises(ValueError, match="missing keys"):
        ChronicleForwarder(
            service_account_json=json.dumps({"client_email": "x"}),
            customer_id="cust",
        )


@pytest.mark.asyncio
async def test_chronicle_mints_jwt_and_exchanges_for_token_and_sends_event():
    sa_json = _make_chronicle_service_account()
    forwarder = ChronicleForwarder(
        service_account_json=sa_json,
        customer_id="cust-1",
        region="us",
    )

    # Two posts: first the OAuth token exchange, then the UDM ingest.
    token_response = _MockResponse(
        status_code=200,
        json_data={"access_token": "ya29.fake", "expires_in": 3600, "token_type": "Bearer"},
    )
    ingest_response = _MockResponse(status_code=200, json_data={"acceptedEventsCount": 1})

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[token_response, ingest_response])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=cm):
        sent = await forwarder.batch_forward([_event(request_id="cri-1")])

    assert sent == 1

    # First call: OAuth token exchange.
    token_call = client.post.await_args_list[0]
    token_url = token_call.args[0] if token_call.args else token_call.kwargs.get("url")
    assert token_url == "https://oauth2.googleapis.com/token"
    grant_data = token_call.kwargs["data"]
    assert grant_data["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    jwt = grant_data["assertion"]
    # JWT is 3 base64url segments separated by dots.
    parts = jwt.split(".")
    assert len(parts) == 3
    # Decode the claims segment and assert iss/scope match the SA.
    padding = "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    assert claims["iss"] == "aegis-test@aegis-test.iam.gserviceaccount.com"
    assert "chronicle-backstory" in claims["scope"]

    # Second call: UDM ingest with bearer token + customerId + events.
    ingest_call = client.post.await_args_list[1]
    ingest_url = ingest_call.args[0] if ingest_call.args else ingest_call.kwargs.get("url")
    assert ingest_url.endswith("/v2/udmevents:batchCreate")
    assert ingest_call.kwargs["headers"]["Authorization"] == "Bearer ya29.fake"
    payload = ingest_call.kwargs["json"]
    assert payload["customerId"] == "cust-1"
    assert payload["events"][0]["additional"]["request_id"] == "cri-1"


@pytest.mark.asyncio
async def test_chronicle_caches_token_across_calls():
    """A second forward within token TTL must reuse the cached access token —
    avoids minting and exchanging a new JWT per event."""
    sa_json = _make_chronicle_service_account()
    forwarder = ChronicleForwarder(
        service_account_json=sa_json,
        customer_id="cust-1",
    )
    token_response = _MockResponse(
        status_code=200,
        json_data={"access_token": "ya29.cached", "expires_in": 3600, "token_type": "Bearer"},
    )
    ingest_response = _MockResponse(status_code=200, json_data={})

    client = AsyncMock()
    client.post = AsyncMock(side_effect=[
        token_response, ingest_response,   # call 1: token + ingest
        ingest_response,                    # call 2: ingest only (cached token)
    ])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=cm):
        await forwarder.batch_forward([_event()])
        await forwarder.batch_forward([_event()])

    # Three posts total: one OAuth, two UDM ingests.
    assert client.post.await_count == 3


# ---------------------------------------------------------------------------
# Credential source — env vs SSM
# ---------------------------------------------------------------------------


def test_resolve_credentials_env_default():
    """When SIEM_CRED_SOURCE is unset/env, the resolver pulls from settings."""
    from services.audit import siem as siem_mod
    with patch.object(siem_mod.settings, "SIEM_CRED_SOURCE", "env"), \
         patch.object(siem_mod.settings, "ELASTIC_API_KEY", "my-elastic-key"):
        out = _resolve_siem_credentials("elastic")
    assert out["ELASTIC_API_KEY"] == "my-elastic-key"


def test_resolve_credentials_ssm_calls_loader():
    """When SIEM_CRED_SOURCE=ssm, ``_load_ssm_credentials`` is invoked."""
    from services.audit import siem as siem_mod
    with patch.object(siem_mod.settings, "SIEM_CRED_SOURCE", "ssm"), \
         patch.object(siem_mod.settings, "SIEM_SSM_PREFIX", "/aegis-siem"), \
         patch.object(
             siem_mod, "_load_ssm_credentials",
             return_value={"ELASTIC_CLOUD_ID": "x", "ELASTIC_API_KEY": "y"},
         ) as mock_load:
        out = _resolve_siem_credentials("elastic")
    mock_load.assert_called_once_with("/aegis-siem", "elastic")
    assert out == {"ELASTIC_CLOUD_ID": "x", "ELASTIC_API_KEY": "y"}


def test_load_ssm_credentials_normalizes_to_upper_snake():
    """``/aegis-siem/elastic/cloud_id`` and ``/aegis-siem/elastic/api_key``
    end up keyed under ``CLOUD_ID``, ``API_KEY``."""
    fake_paginator = MagicMock()
    fake_paginator.paginate = MagicMock(return_value=iter([
        {"Parameters": [
            {"Name": "/aegis-siem/elastic/cloud_id", "Value": "id-1"},
            {"Name": "/aegis-siem/elastic/api_key",  "Value": "key-1"},
        ]},
    ]))
    fake_ssm = MagicMock()
    fake_ssm.get_paginator = MagicMock(return_value=fake_paginator)
    with patch("boto3.client", return_value=fake_ssm):
        out = _load_ssm_credentials("/aegis-siem", "elastic")
    assert out == {"CLOUD_ID": "id-1", "API_KEY": "key-1"}
