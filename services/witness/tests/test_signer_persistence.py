"""Signer key-persistence tests. Real Ed25519 keys, real disk I/O in
tmp_path, real fingerprint stability across resets."""
from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from services.witness import signer as sg


@pytest.fixture(autouse=True)
def _reset_between_tests(monkeypatch):
    """Wipe env + singleton between tests."""
    for var in ("WITNESS_SIGNING_KEY_PEM", "WITNESS_SIGNING_KEY_PATH", "ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    sg._reset_singleton_for_tests()
    yield
    sg._reset_singleton_for_tests()


class TestProductionRefusesEphemeral:
    def test_prod_without_key_raises(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="refusing to boot"):
            sg._resolve_key()

    def test_prod_with_env_pem_ok(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        key = ed25519.Ed25519PrivateKey.generate()
        pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PEM", pem)
        resolved = sg._resolve_key()
        # Same private-key material — fingerprint stable across boots.
        assert resolved.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ) == key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )


class TestDiskPersistence:
    def test_missing_file_generates_and_persists(self, monkeypatch, tmp_path):
        key_path = tmp_path / "witness" / "signing.pem"
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")

        resolved = sg._resolve_key()
        assert key_path.exists()
        # File permissions must be owner-only (0600).
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

        # Signature stable — second call reads the same key back.
        sg._reset_singleton_for_tests()
        again = sg._resolve_key()
        assert (resolved.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ) == again.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ))

    def test_existing_file_loaded_not_regenerated(self, monkeypatch, tmp_path):
        key_path = tmp_path / "signing.pem"
        # Pre-write a key.
        pre = ed25519.Ed25519PrivateKey.generate()
        key_path.write_bytes(pre.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")

        resolved = sg._resolve_key()
        assert resolved.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ) == pre.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def test_corrupted_file_fails_loud(self, monkeypatch, tmp_path):
        """A corrupted key file must raise — not silently generate a new
        key (which would rotate the fingerprint under the operator).
        cryptography raises ValueError on malformed PEM."""
        key_path = tmp_path / "signing.pem"
        key_path.write_bytes(b"NOT-A-PEM")
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(ValueError):
            sg._resolve_key()

    def test_wrong_key_type_rejected(self, monkeypatch, tmp_path):
        """A file containing an RSA key instead of Ed25519 must be
        rejected — witness attestations MUST be Ed25519 per §6.4."""
        from cryptography.hazmat.primitives.asymmetric import rsa
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key_path = tmp_path / "signing.pem"
        key_path.write_bytes(rsa_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="not an Ed25519 key"):
            sg._resolve_key()


class TestDevModeEphemeral:
    def test_dev_without_key_generates_ephemeral(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        # No env-var, no path — dev-mode ephemeral is OK.
        key = sg._resolve_key()
        assert isinstance(key, ed25519.Ed25519PrivateKey)

    def test_no_env_var_defaults_to_dev(self, monkeypatch):
        # ENVIRONMENT unset entirely.
        key = sg._resolve_key()
        assert isinstance(key, ed25519.Ed25519PrivateKey)


class TestFingerprintStabilityForVerifiers:
    def test_attestation_fingerprint_stable_across_reboots(self, monkeypatch, tmp_path):
        """The whole point of this fix: a verifier that cached the
        previous fingerprint sees the SAME fingerprint after a restart."""
        key_path = tmp_path / "signing.pem"
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")

        sg._reset_singleton_for_tests()
        s1 = sg.get_signer()
        fp1 = s1.fingerprint
        pem1 = s1.public_key_pem

        # Simulate a restart — reset the singleton, keep the file on disk.
        sg._reset_singleton_for_tests()
        s2 = sg.get_signer()
        fp2 = s2.fingerprint
        pem2 = s2.public_key_pem

        assert fp1 == fp2, "fingerprint must be stable across restarts"
        assert pem1 == pem2, "public key must be stable across restarts"


class TestAtomicWrite:
    """The write must be atomic — a partial write on disk-full, power
    loss, or SIGKILL must NOT leave a corrupted target that would then
    fail-loud on next boot (and refuse-to-boot in production)."""

    def test_no_tmp_file_left_behind_on_success(self, monkeypatch, tmp_path):
        """After a successful generate, the .tmp sibling must be gone
        (renamed onto the target). Left-behind tmps could accumulate +
        could expose the key material at loose permissions."""
        key_path = tmp_path / "signing.pem"
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")

        sg._resolve_key()
        assert key_path.exists()
        stray = list(tmp_path.glob("signing.pem.tmp.*"))
        assert stray == [], f"tmp files left behind: {stray}"

    def test_partial_write_leaves_old_key_intact(self, monkeypatch, tmp_path):
        """Simulate a failure MID-persist. The pre-existing key file
        must remain valid — verifier stability across a failed rotation
        is the whole point of atomic rename."""
        key_path = tmp_path / "signing.pem"
        good = ed25519.Ed25519PrivateKey.generate()
        good_pem = good.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        key_path.write_bytes(good_pem)

        import os as _os
        def _boom(*_a, **_kw):
            raise OSError("simulated crash before rename")
        monkeypatch.setattr(_os, "rename", _boom)

        with pytest.raises(OSError, match="simulated crash"):
            sg._generate_and_persist(key_path)

        # OLD key material still valid + readable.
        loaded = sg._load_disk_key(key_path)
        assert loaded is not None
        assert loaded.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ) == good.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def test_written_file_permissions_0600(self, monkeypatch, tmp_path):
        """Post-rename file mode is 0600 — no group/other read of a
        signing key at any point."""
        key_path = tmp_path / "signing.pem"
        monkeypatch.setenv("WITNESS_SIGNING_KEY_PATH", str(key_path))
        monkeypatch.setenv("ENVIRONMENT", "production")

        sg._resolve_key()
        mode = key_path.stat().st_mode & 0o777
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"
