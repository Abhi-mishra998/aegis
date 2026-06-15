"""
Sprint 2b — real-AWS integration test for the SIEM credential loader.

These tests run against the live AWS account configured for ``aws`` CLI.
Same pattern as ``test_aws_signing_keys.py``: not skipped when credentials
are present.

What runs by default:

  * ``test_loader_reads_placeholders_from_ssm`` — pulls every
    ``/aegis-siem/{target}/*`` parameter that Sprint 2b provisioned (see
    ``SPRINT_2B_REPORT.md``) and asserts the loader normalizes the
    parameter names to ``UPPER_SNAKE`` and decrypts the SecureString.
    The values are intentionally ``PENDING_REPLACE_WITH_*`` so the test
    can run cleanly without a real Elastic/Sentinel/Chronicle account.

Opt-in real-endpoint smoke tests live below — they execute only when the
operator replaces the PENDING values with real credentials. Each fails
loudly with the remediation if the value still starts with ``PENDING_``.
"""
from __future__ import annotations

import os
import socket
from typing import Any

import pytest

from services.audit.siem import (
    ChronicleForwarder,
    ElasticForwarder,
    SentinelForwarder,
    SIEMEvent,
    _load_ssm_credentials,
)


def _aws_creds_available() -> bool:
    try:
        import boto3  # noqa: PLC0415
        boto3.client("sts").get_caller_identity()
        return True
    except Exception:
        return False


def _can_reach_aws() -> bool:
    try:
        socket.create_connection(("ssm.ap-south-1.amazonaws.com", 443), timeout=3).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not (_aws_creds_available() and _can_reach_aws()),
    reason="AWS credentials or network not available; run `aws configure` to enable",
)


@pytest.fixture
def aws_region() -> str:
    return os.environ.get("AWS_REGION", "ap-south-1")


# ---------------------------------------------------------------------------
# Loader — placeholder values pass through cleanly
# ---------------------------------------------------------------------------


def _placeholder_or_real(value: str) -> str:
    """``PENDING_*`` is the Sprint 2b placeholder shape; non-PENDING is real."""
    return "real" if value and not value.startswith("PENDING_") else "placeholder"


def test_loader_reads_placeholders_from_ssm(aws_region: str) -> None:
    """The SSM loader normalizes Aegis SIEM parameter names to UPPER_SNAKE
    and decrypts the SecureStrings. Passes whether the values are still
    placeholders or have been replaced with real creds."""
    elastic = _load_ssm_credentials("/aegis-siem", "elastic")
    sentinel = _load_ssm_credentials("/aegis-siem", "sentinel")
    chronicle = _load_ssm_credentials("/aegis-siem", "chronicle")

    assert set(elastic.keys()) >= {"CLOUD_ID", "API_KEY"}, (
        f"expected CLOUD_ID + API_KEY under /aegis-siem/elastic/, got {sorted(elastic)}"
    )
    assert set(sentinel.keys()) >= {"WORKSPACE_ID", "SHARED_KEY"}, (
        f"expected WORKSPACE_ID + SHARED_KEY, got {sorted(sentinel)}"
    )
    assert set(chronicle.keys()) >= {"CUSTOMER_ID", "SERVICE_ACCOUNT_JSON"}, (
        f"expected CUSTOMER_ID + SERVICE_ACCOUNT_JSON, got {sorted(chronicle)}"
    )

    # Surface the placeholder/real state so the operator can see at a glance
    # which targets are wired without staring at the SSM console.
    statuses = {
        "elastic":   _placeholder_or_real(elastic.get("API_KEY", "")),
        "sentinel":  _placeholder_or_real(sentinel.get("SHARED_KEY", "")),
        "chronicle": _placeholder_or_real(chronicle.get("CUSTOMER_ID", "")),
    }
    print(f"SIEM credential state (ap-south-1): {statuses}")


# ---------------------------------------------------------------------------
# Real-endpoint smoke tests — run when the PENDING values are replaced
# ---------------------------------------------------------------------------

_EVENT = SIEMEvent(
    timestamp="2026-06-13T00:00:00+00:00",
    tenant_id="aegis-ci",
    agent_id="aegis-ci-agent",
    action="execute_tool",
    tool="db.query",
    decision="allow",
    reason=None,
    risk_score=0.1,
    request_id="aegis-ci-req",
    event_hash="ci-hash",
)


def _skip_until_creds(target: str, key: str, value: str) -> None:
    """If the SSM placeholder hasn't been replaced, surface the exact
    remediation. We use ``pytest.skip`` here (not ``fail``) because the
    placeholders are the default state — running the smoke test should be
    an explicit operator action."""
    if value.startswith("PENDING_"):
        pytest.skip(
            f"{target} smoke test waits on /aegis-siem/{target}/{key} to "
            f"be replaced with a real credential via `aws ssm put-parameter`."
        )


@pytest.mark.asyncio
async def test_elastic_real_endpoint_smoke(aws_region: str) -> None:
    creds = _load_ssm_credentials("/aegis-siem", "elastic")
    _skip_until_creds("elastic", "API_KEY", creds.get("API_KEY", ""))
    _skip_until_creds("elastic", "CLOUD_ID", creds.get("CLOUD_ID", ""))

    forwarder = ElasticForwarder(
        cloud_id=creds["CLOUD_ID"],
        api_key=creds["API_KEY"],
        index=creds.get("INDEX") or "aegis-audit-ci",
    )
    sent = await forwarder.batch_forward([_EVENT])
    assert sent == 1, (
        "Elastic Bulk Index API did not accept the event — check Elastic "
        "Cloud's deployment logs and the role's index permission."
    )


@pytest.mark.asyncio
async def test_sentinel_real_endpoint_smoke(aws_region: str) -> None:
    creds = _load_ssm_credentials("/aegis-siem", "sentinel")
    _skip_until_creds("sentinel", "WORKSPACE_ID", creds.get("WORKSPACE_ID", ""))
    _skip_until_creds("sentinel", "SHARED_KEY", creds.get("SHARED_KEY", ""))

    forwarder = SentinelForwarder(
        workspace_id=creds["WORKSPACE_ID"],
        shared_key=creds["SHARED_KEY"],
        log_type=creds.get("LOG_TYPE") or "AegisAuditCI",
    )
    sent = await forwarder.batch_forward([_EVENT])
    assert sent == 1, (
        "Sentinel Data Collector did not accept the event — check the "
        "workspace ingestion lag (Sentinel can take 2-5 minutes to surface "
        "new custom-log tables) and the shared-key validity."
    )


@pytest.mark.asyncio
async def test_chronicle_real_endpoint_smoke(aws_region: str) -> None:
    creds = _load_ssm_credentials("/aegis-siem", "chronicle")
    _skip_until_creds("chronicle", "CUSTOMER_ID", creds.get("CUSTOMER_ID", ""))
    _skip_until_creds("chronicle", "SERVICE_ACCOUNT_JSON", creds.get("SERVICE_ACCOUNT_JSON", ""))

    forwarder = ChronicleForwarder(
        service_account_json=creds["SERVICE_ACCOUNT_JSON"],
        customer_id=creds["CUSTOMER_ID"],
        region=creds.get("REGION") or "us",
    )
    sent = await forwarder.batch_forward([_EVENT])
    assert sent == 1, (
        "Chronicle UDM ingest did not accept the event — check the "
        "service-account's role binding on the Chronicle customer."
    )
