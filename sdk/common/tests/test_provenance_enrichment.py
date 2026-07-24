"""Tests for the ATF §4.3 provenance-from-env helper. Every field is
independent; empty-string → None normalization; profile hash is
identical whether the env-var is absent or empty."""
from __future__ import annotations

from sdk.common.aegis_profile import (
    AegisProfile,
    ProfileSubject,
)
from sdk.common.aegis_profile import fingerprint as _fp
from sdk.common.provenance_enrichment import enrich_from_env

_ALL_VARS = (
    "AEGIS_MODEL_REF",
    "AEGIS_PROMPT_TEMPLATE_HASH",
    "AEGIS_TOOL_MANIFEST_HASH",
    "AEGIS_CONTAINER_IMAGE_DIGEST",
    "AEGIS_SBOM_REF",
)


def _wipe(monkeypatch):
    for v in _ALL_VARS:
        monkeypatch.delenv(v, raising=False)


class TestEnrichFromEnv:
    def test_no_env_returns_all_none(self, monkeypatch):
        _wipe(monkeypatch)
        p = enrich_from_env()
        assert p.model_ref is None
        assert p.prompt_template_hash is None
        assert p.tool_manifest_hash is None
        assert p.container_image_digest is None
        assert p.sbom_ref is None

    def test_all_env_populates_all(self, monkeypatch):
        _wipe(monkeypatch)
        monkeypatch.setenv("AEGIS_MODEL_REF", "registry://acme/gpt-4o/2026-07-22")
        monkeypatch.setenv("AEGIS_PROMPT_TEMPLATE_HASH", "sha256:aaa")
        monkeypatch.setenv("AEGIS_TOOL_MANIFEST_HASH", "sha256:bbb")
        monkeypatch.setenv("AEGIS_CONTAINER_IMAGE_DIGEST", "sha256:ccc")
        monkeypatch.setenv("AEGIS_SBOM_REF", "sbom://acme/agent/2.3.1")
        p = enrich_from_env()
        assert p.model_ref == "registry://acme/gpt-4o/2026-07-22"
        assert p.prompt_template_hash == "sha256:aaa"
        assert p.tool_manifest_hash == "sha256:bbb"
        assert p.container_image_digest == "sha256:ccc"
        assert p.sbom_ref == "sbom://acme/agent/2.3.1"

    def test_empty_string_normalized_to_none(self, monkeypatch):
        """`AEGIS_MODEL_REF=` (empty) is 'unset' — not 'the empty string
        is a valid model ref'. Profile hash MUST be identical to a
        fully-unset config, else two customers with the same effective
        provenance would ledger different fingerprints."""
        _wipe(monkeypatch)
        monkeypatch.setenv("AEGIS_MODEL_REF", "")
        monkeypatch.setenv("AEGIS_PROMPT_TEMPLATE_HASH", "   ")   # whitespace only
        p = enrich_from_env()
        assert p.model_ref is None
        assert p.prompt_template_hash is None

    def test_whitespace_stripped(self, monkeypatch):
        _wipe(monkeypatch)
        monkeypatch.setenv("AEGIS_MODEL_REF", "  registry://acme/x  ")
        p = enrich_from_env()
        assert p.model_ref == "registry://acme/x"


class TestProfileHashDeterminism:
    """The whole point of the empty→None normalization: identical
    logical provenance MUST produce identical fingerprints."""

    def test_absent_and_empty_env_hash_identically(self, monkeypatch):
        _wipe(monkeypatch)
        p_absent = enrich_from_env()
        prof_absent = AegisProfile(
            subject=ProfileSubject(spiffe_id="spiffe://acme/a/1"),
            human_responsible="scim://acme/Users/lead",
            gate_policy_ref="policy://acme/v17",
            provenance=p_absent,
        )
        fp_absent = _fp(prof_absent)

        monkeypatch.setenv("AEGIS_MODEL_REF", "")
        monkeypatch.setenv("AEGIS_PROMPT_TEMPLATE_HASH", "")
        monkeypatch.setenv("AEGIS_TOOL_MANIFEST_HASH", "")
        monkeypatch.setenv("AEGIS_CONTAINER_IMAGE_DIGEST", "")
        monkeypatch.setenv("AEGIS_SBOM_REF", "")
        p_empty = enrich_from_env()
        prof_empty = AegisProfile(
            subject=ProfileSubject(spiffe_id="spiffe://acme/a/1"),
            human_responsible="scim://acme/Users/lead",
            gate_policy_ref="policy://acme/v17",
            provenance=p_empty,
        )
        fp_empty = _fp(prof_empty)

        assert fp_absent == fp_empty

    def test_populated_provenance_changes_hash(self, monkeypatch):
        """Sanity: setting a real value DOES change the fingerprint —
        otherwise the provenance would be inert."""
        _wipe(monkeypatch)
        base = AegisProfile(
            subject=ProfileSubject(spiffe_id="spiffe://acme/a/1"),
            human_responsible="scim://acme/Users/lead",
            gate_policy_ref="policy://acme/v17",
            provenance=enrich_from_env(),
        )
        fp_base = _fp(base)

        monkeypatch.setenv("AEGIS_MODEL_REF", "registry://acme/gpt-4o")
        populated = AegisProfile(
            subject=ProfileSubject(spiffe_id="spiffe://acme/a/1"),
            human_responsible="scim://acme/Users/lead",
            gate_policy_ref="policy://acme/v17",
            provenance=enrich_from_env(),
        )
        fp_populated = _fp(populated)

        assert fp_base != fp_populated
