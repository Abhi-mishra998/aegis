"""
Sprint 1.3 — tests for the pluggable signing-key custody layer.

Two coverage areas:

  * Unit tests with a hand-rolled ``FakeKmsClient`` — fast, no AWS dependency.
  * One ``integration``-marked test that runs end-to-end against real AWS KMS
    when ``AEGIS_KMS_INTEGRATION=1`` is set in the env (and credentials are
    available). The ops team can flip the flag, point at a CMK in their dev
    account, and prove the path works against the real API.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from sdk.common.signing_keys import (
    AwsKmsSigningKeyProvider,
    LocalFileSigningKeyProvider,
    SigningKeyProvider,
    provider_from_env,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _gen_pem() -> bytes:
    priv = ed25519.Ed25519PrivateKey.generate()
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


class FakeKmsClient:
    """Minimal in-memory KMS stand-in.

    Implements only the surface AwsKmsSigningKeyProvider touches:
    ``encrypt`` and ``decrypt``. The 'ciphertext' is the plaintext prefixed
    with a sentinel and the KeyId, so a mismatched KeyId surfaces as a
    decryption failure exactly as real KMS would.
    """

    SENTINEL = b"FAKE-KMS:"

    def __init__(self, *, key_id: str) -> None:
        self._key_id = key_id

    def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict:
        assert KeyId == self._key_id, f"unknown KeyId {KeyId!r}"
        return {
            "CiphertextBlob": self.SENTINEL + KeyId.encode() + b"||" + Plaintext,
            "KeyId":          KeyId,
        }

    def decrypt(self, *, CiphertextBlob: bytes, KeyId: str | None = None) -> dict:
        if not CiphertextBlob.startswith(self.SENTINEL):
            raise RuntimeError("InvalidCiphertextException: not a KMS blob")
        payload = CiphertextBlob[len(self.SENTINEL):]
        embedded_key, sep, plaintext = payload.partition(b"||")
        if not sep:
            raise RuntimeError("InvalidCiphertextException: malformed blob")
        if embedded_key.decode() != self._key_id:
            raise RuntimeError(
                "InvalidCiphertextException: wrong CMK "
                f"(blob signed by {embedded_key.decode()})"
            )
        if KeyId is not None and KeyId != self._key_id:
            raise RuntimeError(
                "IncorrectKeyException: caller asked for "
                f"{KeyId} but blob is under {self._key_id}"
            )
        return {"Plaintext": plaintext, "KeyId": self._key_id}


class FakeS3Client:
    """Just enough S3 to satisfy AwsKmsSigningKeyProvider's get_object call."""

    def __init__(self, blob: bytes) -> None:
        self._blob = blob

    def get_object(self, *, Bucket: str, Key: str) -> dict:
        class _Body:
            def __init__(self, data: bytes) -> None:
                self._data = data

            def read(self) -> bytes:
                return self._data

        return {"Body": _Body(self._blob)}


# ---------------------------------------------------------------------------
# LocalFile provider
# ---------------------------------------------------------------------------


def test_localfile_loads_from_env(monkeypatch, tmp_path):
    pem = _gen_pem()
    monkeypatch.setenv("RECEIPT_SIGNING_PRIVATE_KEY", base64.b64encode(pem).decode())
    provider = LocalFileSigningKeyProvider(
        env_var_pem="RECEIPT_SIGNING_PRIVATE_KEY",
        disk_path=tmp_path / "key.pem",
        allow_generate=False,
    )
    priv, source = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)
    assert source == "env"
    assert not (tmp_path / "key.pem").exists()  # env wins; disk untouched


def test_localfile_loads_from_disk(tmp_path):
    pem = _gen_pem()
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    provider = LocalFileSigningKeyProvider(
        env_var_pem="UNUSED_ENV",
        disk_path=path,
        allow_generate=False,
    )
    priv, source = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)
    assert source.startswith("disk:")


def test_localfile_refuses_to_generate_when_disallowed(tmp_path):
    provider = LocalFileSigningKeyProvider(
        env_var_pem="UNUSED_ENV",
        disk_path=tmp_path / "missing.pem",
        allow_generate=False,
    )
    with pytest.raises(RuntimeError, match="no signing key"):
        provider.load()


def test_localfile_generates_and_persists(tmp_path):
    path = tmp_path / "fresh.pem"
    provider = LocalFileSigningKeyProvider(
        env_var_pem="UNUSED_ENV",
        disk_path=path,
        allow_generate=True,
    )
    priv, source = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)
    assert source.startswith("generated:")
    assert path.exists()


# ---------------------------------------------------------------------------
# KMS provider (unit-tested with FakeKmsClient)
# ---------------------------------------------------------------------------

KEY_ID = "arn:aws:kms:ap-south-1:111111111111:key/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_kms_decrypts_inline_ciphertext():
    pem = _gen_pem()
    kms = FakeKmsClient(key_id=KEY_ID)
    ciphertext = kms.encrypt(KeyId=KEY_ID, Plaintext=pem)["CiphertextBlob"]

    provider = AwsKmsSigningKeyProvider(
        key_id=KEY_ID,
        ciphertext=ciphertext,
        kms_client=kms,
    )
    priv, source = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)
    assert source == f"kms:{KEY_ID}"


def test_kms_decrypts_via_s3_uri():
    pem = _gen_pem()
    kms = FakeKmsClient(key_id=KEY_ID)
    ciphertext = kms.encrypt(KeyId=KEY_ID, Plaintext=pem)["CiphertextBlob"]
    s3 = FakeS3Client(ciphertext)

    provider = AwsKmsSigningKeyProvider(
        key_id=KEY_ID,
        s3_uri="s3://aegis-audit-keys/receipt-key.bin",
        kms_client=kms,
        s3_client=s3,
    )
    priv, _ = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)


def test_kms_rejects_blob_under_wrong_cmk():
    pem = _gen_pem()
    wrong_cmk = FakeKmsClient(key_id="arn:aws:kms:ap-south-1:111111111111:key/other")
    ciphertext = wrong_cmk.encrypt(KeyId=wrong_cmk._key_id, Plaintext=pem)["CiphertextBlob"]
    right_cmk = FakeKmsClient(key_id=KEY_ID)

    provider = AwsKmsSigningKeyProvider(
        key_id=KEY_ID,
        ciphertext=ciphertext,
        kms_client=right_cmk,
    )
    with pytest.raises(RuntimeError, match="InvalidCiphertext|IncorrectKey"):
        provider.load()


def test_kms_provider_requires_ciphertext_or_s3():
    with pytest.raises(ValueError, match="ciphertext or s3_uri"):
        AwsKmsSigningKeyProvider(key_id=KEY_ID)


def test_kms_provider_rejects_malformed_s3_uri():
    kms = FakeKmsClient(key_id=KEY_ID)
    s3 = FakeS3Client(b"x")
    provider = AwsKmsSigningKeyProvider(
        key_id=KEY_ID,
        s3_uri="not-an-s3-uri",
        kms_client=kms,
        s3_client=s3,
    )
    with pytest.raises(ValueError, match="not an s3:// URI"):
        provider.load()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_defaults_to_localfile(monkeypatch, tmp_path):
    monkeypatch.delenv("RECEIPT_SIGNING_PROVIDER", raising=False)
    provider = provider_from_env(
        provider_env="RECEIPT_SIGNING_PROVIDER",
        pem_env="RECEIPT_SIGNING_PRIVATE_KEY",
        disk_path=tmp_path / "k.pem",
        kms_key_id_env="X_KMS_KEY",
        kms_blob_env="X_KMS_BLOB",
        kms_s3_uri_env="X_KMS_S3",
    )
    assert isinstance(provider, LocalFileSigningKeyProvider)


def test_factory_selects_kms_when_env_set(monkeypatch, tmp_path):
    pem = _gen_pem()
    kms = FakeKmsClient(key_id=KEY_ID)
    ciphertext = kms.encrypt(KeyId=KEY_ID, Plaintext=pem)["CiphertextBlob"]

    monkeypatch.setenv("RECEIPT_SIGNING_PROVIDER", "kms")
    monkeypatch.setenv("RECEIPT_SIGNING_KMS_KEY_ID", KEY_ID)
    monkeypatch.setenv("RECEIPT_SIGNING_KMS_CIPHERTEXT_B64", base64.b64encode(ciphertext).decode())

    provider = provider_from_env(
        provider_env="RECEIPT_SIGNING_PROVIDER",
        pem_env="RECEIPT_SIGNING_PRIVATE_KEY",
        disk_path=tmp_path / "k.pem",
        kms_key_id_env="RECEIPT_SIGNING_KMS_KEY_ID",
        kms_blob_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_B64",
        kms_s3_uri_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI",
    )
    assert isinstance(provider, AwsKmsSigningKeyProvider)
    # Verify the provider can load if we hand it the fake KMS client.
    provider._kms = kms  # noqa: SLF001 — surgical test injection
    priv, _ = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)


def test_factory_rejects_kms_without_key_id(monkeypatch, tmp_path):
    monkeypatch.setenv("RECEIPT_SIGNING_PROVIDER", "kms")
    monkeypatch.delenv("RECEIPT_SIGNING_KMS_KEY_ID", raising=False)
    monkeypatch.delenv("RECEIPT_SIGNING_KMS_CIPHERTEXT_B64", raising=False)
    with pytest.raises(RuntimeError, match="requires RECEIPT_SIGNING_KMS_KEY_ID"):
        provider_from_env(
            provider_env="RECEIPT_SIGNING_PROVIDER",
            pem_env="RECEIPT_SIGNING_PRIVATE_KEY",
            disk_path=tmp_path / "k.pem",
            kms_key_id_env="RECEIPT_SIGNING_KMS_KEY_ID",
            kms_blob_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_B64",
            kms_s3_uri_env="RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI",
        )


# ---------------------------------------------------------------------------
# Optional integration test against real AWS KMS
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_real_kms_round_trip_when_creds_present():
    """End-to-end against the real AWS KMS API.

    Skipped unless ``AEGIS_KMS_INTEGRATION=1`` and ``AEGIS_KMS_TEST_KEY_ID``
    are set in the env. Intended for the operator to flip on, point at a CMK
    in their dev account (kms:Encrypt + kms:Decrypt permissions), and prove
    the boto3 wire works against the live API.
    """
    if os.environ.get("AEGIS_KMS_INTEGRATION") != "1":
        pytest.skip("set AEGIS_KMS_INTEGRATION=1 to run KMS integration tests")
    key_id = os.environ.get("AEGIS_KMS_TEST_KEY_ID")
    if not key_id:
        pytest.skip("set AEGIS_KMS_TEST_KEY_ID to a CMK ARN/Alias to run this test")

    try:
        import boto3
    except ImportError:
        pytest.skip("boto3 not installed")

    region = os.environ.get("AWS_REGION", "ap-south-1")
    kms = boto3.client("kms", region_name=region)
    pem = _gen_pem()
    ciphertext = kms.encrypt(KeyId=key_id, Plaintext=pem)["CiphertextBlob"]

    provider = AwsKmsSigningKeyProvider(
        key_id=key_id,
        ciphertext=ciphertext,
        kms_client=kms,
        region_name=region,
    )
    priv, source = provider.load()
    assert isinstance(priv, ed25519.Ed25519PrivateKey)
    assert source == f"kms:{key_id}"
