"""Sprint 9 — Production enforcement on the signing-key provider factory.

The audit's S7 finding: signing keys live on the container filesystem
in production. Sprint 1.3 shipped KMS + SSM providers but the factory
silently fell back to the local-file path if the operator forgot to
set the env vars. Sprint 9 closes that gap.

These tests pin the contract:

  * AEGIS_ENV=prod (or "production") REFUSES the LocalFile fallback.
  * AEGIS_ENV unset / "dev" / anything else still allows it (so dev
    laptops + CI still boot without SSM).
  * The explicit ``ssm`` / ``kms`` providers still construct cleanly
    when their required env is set — the prod guard never blocks them.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from sdk.common.signing_keys import (
    AwsKmsSigningKeyProvider,
    LocalFileSigningKeyProvider,
    SsmSigningKeyProvider,
    provider_from_env,
)


_PROVIDER_ENV = "RECEIPT_SIGNING_PROVIDER"
_PEM_ENV = "RECEIPT_SIGNING_KEY_PEM"
_DISK_PATH = Path("/tmp/aegis-test-receipt-signing.pem")
_KMS_KEY_ENV = "RECEIPT_SIGNING_KMS_KEY_ID"
_KMS_BLOB_ENV = "RECEIPT_SIGNING_KMS_CIPHERTEXT_B64"
_KMS_S3_ENV = "RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI"
_SSM_PARAM_ENV = "RECEIPT_SIGNING_SSM_PARAMETER"


def _factory(**kwargs):
    return provider_from_env(
        provider_env=_PROVIDER_ENV,
        pem_env=_PEM_ENV,
        disk_path=_DISK_PATH,
        kms_key_id_env=_KMS_KEY_ENV,
        kms_blob_env=_KMS_BLOB_ENV,
        kms_s3_uri_env=_KMS_S3_ENV,
        ssm_parameter_env=_SSM_PARAM_ENV,
        **kwargs,
    )


def _clear_env(monkeypatch) -> None:
    for var in (
        "AEGIS_ENV",
        _PROVIDER_ENV,
        _PEM_ENV,
        _KMS_KEY_ENV,
        _KMS_BLOB_ENV,
        _KMS_S3_ENV,
        _SSM_PARAM_ENV,
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# Dev / default behaviour — LocalFile fallback still works
# ---------------------------------------------------------------------------


def test_unset_env_falls_back_to_local(monkeypatch) -> None:
    _clear_env(monkeypatch)
    provider = _factory(allow_generate=True)
    assert isinstance(provider, LocalFileSigningKeyProvider)


def test_dev_env_falls_back_to_local(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "dev")
    provider = _factory(allow_generate=True)
    assert isinstance(provider, LocalFileSigningKeyProvider)


def test_staging_env_falls_back_to_local(monkeypatch) -> None:
    # We intentionally only enforce on the literal "prod" / "production"
    # values. Staging should match prod posture in practice but the
    # guard's blast radius is scoped narrowly to avoid surprises.
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "staging")
    provider = _factory(allow_generate=True)
    assert isinstance(provider, LocalFileSigningKeyProvider)


# ---------------------------------------------------------------------------
# Prod enforcement — the load-bearing Sprint 9 contract
# ---------------------------------------------------------------------------


def test_prod_env_refuses_local_fallback(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "prod")
    with pytest.raises(RuntimeError) as exc:
        _factory(allow_generate=True)
    msg = str(exc.value)
    assert "AEGIS_ENV=prod" in msg
    assert _PROVIDER_ENV in msg
    # The error names the explicit path the operator must set so the
    # fix is obvious from the log line alone.
    assert "ssm" in msg.lower() or "kms" in msg.lower()


def test_production_env_alias_also_refused(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "production")
    with pytest.raises(RuntimeError):
        _factory(allow_generate=True)


def test_prod_case_insensitive(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "PROD")
    with pytest.raises(RuntimeError):
        _factory(allow_generate=True)


# ---------------------------------------------------------------------------
# Explicit ssm/kms providers are NEVER blocked by the prod guard
# ---------------------------------------------------------------------------


def test_prod_with_ssm_provider_constructs(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "prod")
    monkeypatch.setenv(_PROVIDER_ENV, "ssm")
    monkeypatch.setenv(_SSM_PARAM_ENV, "/aegis-audit/receipt-signing-key")
    provider = _factory(allow_generate=False)
    assert isinstance(provider, SsmSigningKeyProvider)


def test_prod_with_kms_provider_constructs(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "prod")
    monkeypatch.setenv(_PROVIDER_ENV, "kms")
    monkeypatch.setenv(_KMS_KEY_ENV, "arn:aws:kms:ap-south-1:111:key/abc")
    monkeypatch.setenv(_KMS_BLOB_ENV, base64.b64encode(b"ciphertext").decode("ascii"))
    provider = _factory(allow_generate=False)
    assert isinstance(provider, AwsKmsSigningKeyProvider)


def test_prod_with_kms_provider_missing_key_id_still_errors(monkeypatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "prod")
    monkeypatch.setenv(_PROVIDER_ENV, "kms")
    # Forgot to set KMS_KEY_ID — should error with the existing message,
    # not the new prod-guard message.
    with pytest.raises(RuntimeError) as exc:
        _factory(allow_generate=False)
    assert _KMS_KEY_ENV in str(exc.value)


def test_explicit_unknown_provider_in_prod_still_blocked(monkeypatch) -> None:
    # A typo'd ``RECEIPT_SIGNING_PROVIDER=ssmm`` falls through to the
    # local branch — the prod guard catches it. This is the most likely
    # real-world misconfiguration.
    _clear_env(monkeypatch)
    monkeypatch.setenv("AEGIS_ENV", "prod")
    monkeypatch.setenv(_PROVIDER_ENV, "ssmm")  # typo
    with pytest.raises(RuntimeError) as exc:
        _factory(allow_generate=True)
    assert "AEGIS_ENV=prod" in str(exc.value)
