"""
Sprint 1.3 — pluggable key custody for the audit chain's ed25519 signing keys.

The audit (C9, S5) found that production signing keys were read directly from
``/data/keys/receipt-signing.pem`` on the audit container's filesystem — the
same blast radius as the database. Any attacker who compromises the audit
container has the key, can re-sign tampered rows, and the cryptographic chain
goes from "tamper-evident" to "theatre."

This module introduces a provider abstraction so the audit service no longer
cares *where* the key lives — only that it can ask for a loaded
:class:`cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey`.
Three providers ship:

* :class:`SsmSigningKeyProvider` — the **production** path. The ed25519 PEM
  lives as a ``SecureString`` parameter in AWS Systems Manager Parameter
  Store, encrypted at rest under a KMS key (the default ``alias/aws/ssm`` or
  a customer-managed key). The audit service fetches the plaintext via
  ``ssm:GetParameter(WithDecryption=True)`` at boot — the PEM never lands on
  disk and key rotation is a single ``ssm:PutParameter`` call. CloudTrail
  records every access.
* :class:`AwsKmsSigningKeyProvider` — envelope-encryption path. The PEM is
  KMS-encrypted at rest under a Customer Master Key the operator provides; the
  audit service calls ``kms:Decrypt`` at boot. Useful when the PEM blob is too
  big for SSM Parameter Store's 4096-byte limit (rare for ed25519) or when
  multiple services share the same wrapped blob via S3.
* :class:`LocalFileSigningKeyProvider` — dev-only. Reads/writes a PEM on disk.
  Refuses to silently generate a key in production by default.

Selection happens through env vars. The factory ``provider_from_env`` picks
SSM when ``RECEIPT_SIGNING_PROVIDER=ssm`` (or, equivalently, when
``RECEIPT_SIGNING_SSM_PARAMETER`` is set with no explicit provider choice):

    RECEIPT_SIGNING_PROVIDER=ssm
    RECEIPT_SIGNING_SSM_PARAMETER=/aegis-audit/receipt-signing-key
    AWS_REGION=ap-south-1

KMS envelope-encryption alternative::

    RECEIPT_SIGNING_PROVIDER=kms
    RECEIPT_SIGNING_KMS_KEY_ID=arn:aws:kms:ap-south-1:.../key/...
    RECEIPT_SIGNING_KMS_CIPHERTEXT_B64=<base64 of kms.Encrypt(PEM)>

LocalFile remains the *fallback default* so unconfigured dev runs continue to
work — but every production deployment must select SSM or KMS explicitly.
"""
from __future__ import annotations

import abc
import base64
import os
from pathlib import Path
from typing import Any

import structlog
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class SigningKeyProvider(abc.ABC):
    """Returns a loaded ed25519 private key and a human-readable source label.

    A provider must be deterministic across calls — the audit service caches
    the resulting :class:`ReceiptSigner` and only consults the provider once.
    """

    @abc.abstractmethod
    def load(self) -> tuple[ed25519.Ed25519PrivateKey, str]:
        """Return ``(private_key, source_label)`` or raise on failure.

        ``source_label`` lands in the ``receipt_signer_ready`` log so an
        operator can confirm which provider satisfied the boot.
        """


# ---------------------------------------------------------------------------
# Local file provider (preserves pre-Sprint-1 behaviour, dev-only)
# ---------------------------------------------------------------------------


class LocalFileSigningKeyProvider(SigningKeyProvider):
    """Loads the private key from an env-var PEM, then a filesystem path,
    generating a fresh one only as the last resort (test environments).

    This is the legacy path — production should use
    :class:`AwsKmsSigningKeyProvider` instead.
    """

    def __init__(
        self,
        env_var_pem: str,
        disk_path: Path,
        *,
        allow_generate: bool = True,
    ) -> None:
        self._env_var_pem = env_var_pem
        self._disk_path = disk_path
        self._allow_generate = allow_generate

    def load(self) -> tuple[ed25519.Ed25519PrivateKey, str]:
        raw = os.environ.get(self._env_var_pem)
        if raw:
            try:
                pem = base64.b64decode(raw)
            except Exception:
                pem = raw.encode("ascii")
            return _load_pem(pem), "env"

        if self._disk_path.exists():
            return _load_pem(self._disk_path.read_bytes()), f"disk:{self._disk_path}"

        if not self._allow_generate:
            raise RuntimeError(
                f"no signing key available — set {self._env_var_pem} "
                f"or place a PEM at {self._disk_path}"
            )

        priv = ed25519.Ed25519PrivateKey.generate()
        try:
            self._disk_path.parent.mkdir(parents=True, exist_ok=True)
            self._disk_path.write_bytes(
                priv.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption(),
                )
            )
            try:
                self._disk_path.chmod(0o600)
            except OSError:
                pass
            return priv, f"generated:{self._disk_path}"
        except OSError:
            log.warning("signing_key_ephemeral", reason="cannot_persist", path=str(self._disk_path))
            return priv, "generated:ephemeral"


# ---------------------------------------------------------------------------
# AWS SSM Parameter Store provider — the production default for new deployments
# ---------------------------------------------------------------------------


class SsmSigningKeyProvider(SigningKeyProvider):
    """Loads the ed25519 PEM directly from AWS Systems Manager Parameter Store.

    The parameter is expected to be a ``SecureString`` so SSM transparently
    encrypts/decrypts under a KMS key (typically ``alias/aws/ssm`` or a
    customer-managed CMK). The audit service calls
    ``ssm:GetParameter(WithDecryption=True)`` once at boot and holds the PEM
    in memory only. CloudTrail records the access. Rotation is one
    ``ssm:PutParameter`` call — no application restart required, only the
    next signer-cache invalidation.

    Required IAM on the audit service's role:
      ``ssm:GetParameter`` on the parameter ARN, and
      ``kms:Decrypt`` on whichever CMK encrypts the SecureString.
    """

    def __init__(
        self,
        parameter_name: str,
        *,
        ssm_client: Any = None,
        region_name: str | None = None,
    ) -> None:
        if not parameter_name:
            raise ValueError("parameter_name is required")
        self._parameter_name = parameter_name
        self._ssm = ssm_client
        self._region = region_name

    def _client(self) -> Any:
        if self._ssm is not None:
            return self._ssm
        import boto3  # noqa: PLC0415 — only imported when the SSM path actually runs
        self._ssm = boto3.client("ssm", region_name=self._region)
        return self._ssm

    def load(self) -> tuple[ed25519.Ed25519PrivateKey, str]:
        resp = self._client().get_parameter(
            Name=self._parameter_name,
            WithDecryption=True,
        )
        param = resp.get("Parameter") or {}
        value = param.get("Value", "")
        if not value:
            raise RuntimeError(
                f"SSM parameter {self._parameter_name!r} returned an empty value"
            )
        pem = _b64_or_raw_pem(value)
        priv = _load_pem(pem)
        log.info(
            "ssm_signing_key_loaded",
            parameter=self._parameter_name,
            region=self._region or "default",
            version=param.get("Version"),
        )
        return priv, f"ssm:{self._parameter_name}"


def _b64_or_raw_pem(value: str | bytes) -> bytes:
    """SSM parameters may store the PEM as raw text or base64. Accept either."""
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    stripped = value.strip()
    if stripped.startswith("-----BEGIN"):
        return stripped.encode("ascii")
    try:
        return base64.b64decode(stripped)
    except Exception:
        return stripped.encode("ascii")


# ---------------------------------------------------------------------------
# AWS KMS envelope-encryption provider
# ---------------------------------------------------------------------------


class AwsKmsSigningKeyProvider(SigningKeyProvider):
    """Loads the ed25519 PEM by decrypting a KMS-wrapped blob.

    Why envelope encryption rather than KMS Sign/Verify? AWS KMS asymmetric
    keys do not support ed25519 (P-256/P-384/RSA only). Migrating the receipt
    scheme to ECDSA would invalidate every historical receipt in the chain.
    Instead we encrypt the raw PEM under a CMK: at boot the audit service
    calls ``kms.Decrypt`` once, holds the PEM in memory, and signs receipts
    locally with the existing ed25519 code. CloudTrail records every
    ``kms.Decrypt`` so unexpected key access is detectable.

    The encrypted blob can live in:

    * ``RECEIPT_SIGNING_KMS_CIPHERTEXT_B64`` — base64-encoded ciphertext
      from ``kms.Encrypt`` (for small CMK-wrapped payloads, typical case)
    * ``RECEIPT_SIGNING_KMS_CIPHERTEXT_S3_URI`` — ``s3://bucket/key`` pointer
      for blobs above the env-var size limit; the IAM role needs s3:GetObject
    """

    def __init__(
        self,
        key_id: str,
        ciphertext: bytes | None = None,
        s3_uri: str | None = None,
        *,
        kms_client: Any = None,
        s3_client: Any = None,
        region_name: str | None = None,
    ) -> None:
        if not ciphertext and not s3_uri:
            raise ValueError(
                "AwsKmsSigningKeyProvider needs either ciphertext or s3_uri"
            )
        self._key_id = key_id
        self._ciphertext = ciphertext
        self._s3_uri = s3_uri
        self._kms = kms_client
        self._s3 = s3_client
        self._region = region_name

    def _kms_client(self) -> Any:
        if self._kms is not None:
            return self._kms
        import boto3  # noqa: PLC0415 — only imported when KMS path actually runs
        self._kms = boto3.client("kms", region_name=self._region)
        return self._kms

    def _s3_client(self) -> Any:
        if self._s3 is not None:
            return self._s3
        import boto3  # noqa: PLC0415
        self._s3 = boto3.client("s3", region_name=self._region)
        return self._s3

    def _fetch_ciphertext(self) -> bytes:
        if self._ciphertext is not None:
            return self._ciphertext
        assert self._s3_uri is not None
        # s3://bucket/path/to/key
        prefix = "s3://"
        if not self._s3_uri.startswith(prefix):
            raise ValueError(f"not an s3:// URI: {self._s3_uri!r}")
        bucket, _, key = self._s3_uri[len(prefix):].partition("/")
        if not bucket or not key:
            raise ValueError(f"malformed s3:// URI: {self._s3_uri!r}")
        resp = self._s3_client().get_object(Bucket=bucket, Key=key)
        return resp["Body"].read()

    def load(self) -> tuple[ed25519.Ed25519PrivateKey, str]:
        ciphertext = self._fetch_ciphertext()
        # KeyId is optional for symmetric CMKs but always passed so a key
        # alias mismatch surfaces as a clear error rather than a silent decrypt
        # against the "wrong" CMK.
        resp = self._kms_client().decrypt(
            CiphertextBlob=ciphertext,
            KeyId=self._key_id,
        )
        plaintext = resp["Plaintext"]
        priv = _load_pem(plaintext)
        log.info(
            "kms_signing_key_loaded",
            key_id=self._key_id,
            region=self._region or "default",
        )
        return priv, f"kms:{self._key_id}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def provider_from_env(
    *,
    provider_env: str,
    pem_env: str,
    disk_path: Path,
    kms_key_id_env: str,
    kms_blob_env: str,
    kms_s3_uri_env: str,
    ssm_parameter_env: str | None = None,
    region_env: str = "AWS_REGION",
    allow_generate: bool = True,
) -> SigningKeyProvider:
    """Pick a provider based on env vars.

    Precedence:
      1. ``<provider_env>=ssm`` (or ``<ssm_parameter_env>`` set with no
         explicit provider choice) → :class:`SsmSigningKeyProvider`.
         This is the production-default path.
      2. ``<provider_env>=kms`` → :class:`AwsKmsSigningKeyProvider`
         (envelope encryption against a customer-managed CMK).
      3. Anything else (including unset) → :class:`LocalFileSigningKeyProvider`
         (dev only, never for production).
    """
    region = os.environ.get(region_env) or None
    explicit_choice = (os.environ.get(provider_env) or "").strip().lower()
    ssm_param = (
        os.environ.get(ssm_parameter_env, "").strip()
        if ssm_parameter_env
        else ""
    )

    if explicit_choice == "ssm" or (not explicit_choice and ssm_param):
        if not ssm_param:
            raise RuntimeError(
                f"{provider_env}=ssm requires {ssm_parameter_env} "
                f"to point at a SecureString parameter (e.g. "
                f"/aegis-audit/receipt-signing-key)"
            )
        return SsmSigningKeyProvider(parameter_name=ssm_param, region_name=region)

    if explicit_choice == "kms":
        key_id = os.environ.get(kms_key_id_env, "").strip()
        if not key_id:
            raise RuntimeError(
                f"{provider_env}=kms requires {kms_key_id_env} to be set"
            )
        blob_b64 = os.environ.get(kms_blob_env, "").strip()
        s3_uri = os.environ.get(kms_s3_uri_env, "").strip()
        if not blob_b64 and not s3_uri:
            raise RuntimeError(
                f"{provider_env}=kms requires one of "
                f"{kms_blob_env} or {kms_s3_uri_env}"
            )
        ciphertext = base64.b64decode(blob_b64) if blob_b64 else None
        return AwsKmsSigningKeyProvider(
            key_id=key_id,
            ciphertext=ciphertext,
            s3_uri=s3_uri or None,
            region_name=region,
        )

    # Sprint 9 — Production enforcement.
    #
    # In any environment that announces itself as production
    # (``AEGIS_ENV=prod``), the LocalFile fallback is REFUSED. Without
    # this guard a misconfigured prod EC2 silently boots with an
    # on-disk PEM that is neither rotated nor recoverable after a node
    # failure — the audit's S7 finding (signing keys on container
    # filesystem) lives on. We fail closed at process start so the
    # mistake is visible at deploy time, not after a forensic incident.
    aegis_env = (os.environ.get("AEGIS_ENV") or "").strip().lower()
    if aegis_env in {"prod", "production"}:
        raise RuntimeError(
            f"AEGIS_ENV={aegis_env} but {provider_env} is unset / unrecognised "
            f"({explicit_choice!r}). Production deployments MUST use the SSM "
            f"or KMS path — set {provider_env}=ssm with {ssm_parameter_env}, "
            f"or {provider_env}=kms with {kms_key_id_env} + "
            f"{kms_blob_env}/{kms_s3_uri_env}."
        )

    return LocalFileSigningKeyProvider(
        env_var_pem=pem_env,
        disk_path=disk_path,
        allow_generate=allow_generate,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_pem(pem: bytes) -> ed25519.Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise ValueError("private key is not ed25519")
    return key
