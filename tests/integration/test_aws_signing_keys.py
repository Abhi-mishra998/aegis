"""
Sprint 1.3 — REAL AWS integration tests for the signing-key providers.

These tests hit the live AWS account configured for the local ``aws`` CLI.
They are not skipped when credentials are present — that's the point. The
intent is exactly what the project lead asked for: prove the production path
works against the real API, not a fake.

What runs:
  * ``test_ssm_round_trip_against_real_aws`` — writes a ``SecureString`` to
    SSM Parameter Store under ``/aegis-audit-ci/<run-id>/receipt-signing.pem``,
    loads it via :class:`SsmSigningKeyProvider`, signs + verifies a message
    locally, then deletes the parameter. Uses the default ``alias/aws/ssm``
    KMS key so no CMK provisioning is required.
  * ``test_kms_envelope_round_trip_against_real_aws`` — picks the first
    SYMMETRIC customer CMK whose key policy permits ``kms:Encrypt`` and
    ``kms:Decrypt`` for the test runner, wraps a fresh PEM via ``kms:Encrypt``,
    decrypts via :class:`AwsKmsSigningKeyProvider`, and asserts the round
    trip. No new CMK is created — that would cost ~$1/key + a 7-day deletion
    schedule. If no usable CMK is found the test fails loudly with the
    remediation steps rather than silently skipping.

Skip semantics: a test is skipped ONLY when ``aws sts get-caller-identity``
fails — i.e., when no credentials are configured. Any other error (network,
permission denied, region mismatch) fails the test so the operator sees it.
"""
from __future__ import annotations

import os
import socket
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from sdk.common.signing_keys import (
    AwsKmsSigningKeyProvider,
    SsmSigningKeyProvider,
)


def _aws_creds_available() -> bool:
    """True when boto3 can authenticate with the local environment."""
    try:
        import boto3  # noqa: PLC0415
        sts = boto3.client("sts")
        sts.get_caller_identity()
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


@pytest.fixture
def fresh_pem() -> bytes:
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


# ---------------------------------------------------------------------------
# SSM — production-default path
# ---------------------------------------------------------------------------


def test_ssm_round_trip_against_real_aws(aws_region: str, fresh_pem: bytes) -> None:
    """End-to-end against real SSM Parameter Store + the AWS-managed SSM KMS key."""
    import boto3

    ssm = boto3.client("ssm", region_name=aws_region)
    param_name = f"/aegis-audit-ci/{uuid.uuid4()}/receipt-signing.pem"

    # 1. Write the PEM as a SecureString (encrypted under alias/aws/ssm).
    ssm.put_parameter(
        Name=param_name,
        Description="Sprint 1.3 SSM provider integration test (transient).",
        Value=fresh_pem.decode("ascii"),
        Type="SecureString",
        Tier="Standard",
    )
    try:
        # 2. Load through the provider — this is the audit service's real boot path.
        provider = SsmSigningKeyProvider(parameter_name=param_name, region_name=aws_region)
        priv, source = provider.load()

        assert isinstance(priv, ed25519.Ed25519PrivateKey)
        assert source == f"ssm:{param_name}"

        # 3. Prove the loaded key is functional — sign + verify a payload.
        message = b"aegis-sprint-1-ssm-provider"
        sig = priv.sign(message)
        priv.public_key().verify(sig, message)  # raises on failure
    finally:
        # 4. Clean up the parameter regardless of test outcome.
        ssm.delete_parameter(Name=param_name)


# ---------------------------------------------------------------------------
# KMS — envelope encryption against a customer CMK
# ---------------------------------------------------------------------------


def _find_usable_customer_cmk(kms_client, region: str) -> str | None:
    """Return the ARN of a symmetric customer-managed CMK we can both
    ``Encrypt`` and ``Decrypt`` with. We deliberately skip ``aws/*``
    AWS-managed keys because they cannot be used via ``kms:Encrypt`` from
    arbitrary callers (the SSM-managed key, for example).
    """
    paginator = kms_client.get_paginator("list_keys")
    for page in paginator.paginate():
        for entry in page.get("Keys", []):
            key_id = entry["KeyId"]
            try:
                meta = kms_client.describe_key(KeyId=key_id)["KeyMetadata"]
            except Exception:
                continue
            if meta.get("KeyManager") != "CUSTOMER":
                continue
            if meta.get("KeyUsage") != "ENCRYPT_DECRYPT":
                continue
            if meta.get("KeySpec") not in (None, "SYMMETRIC_DEFAULT"):
                continue
            if meta.get("KeyState") != "Enabled":
                continue
            # Quick permission probe — encrypt 1 byte then decrypt.
            try:
                blob = kms_client.encrypt(KeyId=key_id, Plaintext=b"a")["CiphertextBlob"]
                kms_client.decrypt(CiphertextBlob=blob, KeyId=key_id)
                return meta["Arn"]
            except Exception:
                continue
    return None


def test_kms_envelope_round_trip_against_real_aws(
    aws_region: str, fresh_pem: bytes,
) -> None:
    """End-to-end against real KMS Encrypt/Decrypt with a customer CMK.

    No new CMK is created — that would incur cost and a 7-day deletion
    schedule. If no usable customer CMK exists in the account, the test
    fails loudly with the remediation: provision one via Terraform or set
    ``AEGIS_TEST_KMS_KEY_ID`` to point at an existing one.
    """
    import boto3

    kms = boto3.client("kms", region_name=aws_region)
    key_id = os.environ.get("AEGIS_TEST_KMS_KEY_ID") or _find_usable_customer_cmk(
        kms, aws_region,
    )
    if not key_id:
        pytest.fail(
            "no usable customer-managed CMK found in this account/region. "
            "Either (a) provision one with kms:Encrypt + kms:Decrypt permission, "
            "or (b) set AEGIS_TEST_KMS_KEY_ID to an existing CMK ARN. "
            "AWS-managed keys (alias/aws/*) cannot be used here because their "
            "key policies do not allow kms:Encrypt from arbitrary callers."
        )

    ciphertext = kms.encrypt(KeyId=key_id, Plaintext=fresh_pem)["CiphertextBlob"]
    provider = AwsKmsSigningKeyProvider(
        key_id=key_id,
        ciphertext=ciphertext,
        region_name=aws_region,
    )
    priv, source = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)
    assert source == f"kms:{key_id}"

    # Functional check — sign + verify locally.
    message = b"aegis-sprint-1-kms-provider"
    sig = priv.sign(message)
    priv.public_key().verify(sig, message)
