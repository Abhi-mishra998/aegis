"""ATF v3.2 §4.3 — Aegis Profile provenance enrichment.

Reads standard CI/deploy-time env vars that a customer's pipeline
sets and produces a populated `ProfileProvenance`. Missing vars stay
`None` — we never fabricate provenance because a null field is
honest ("we didn't have this at mint time") while a fake value
misleads the auditor.

The env-var name shape is deliberately explicit (`AEGIS_...`) so an
operator grep'ing their CI config sees exactly which values will
land in the profile.
"""
from __future__ import annotations

import os

from sdk.common.aegis_profile import ProfileProvenance


def _read(name: str) -> str | None:
    """Env-var read with empty-string → None normalization. An operator
    who sets `AEGIS_MODEL_REF=` (empty) means "unset", not "the empty
    string is a valid model ref" — the profile hash should be identical
    to a fully unset config."""
    v = os.getenv(name, "")
    v = v.strip()
    return v or None


def enrich_from_env() -> ProfileProvenance:
    """Build a `ProfileProvenance` from the standard env-var contract:

        AEGIS_MODEL_REF               → provenance.model_ref
        AEGIS_PROMPT_TEMPLATE_HASH    → provenance.prompt_template_hash
        AEGIS_TOOL_MANIFEST_HASH      → provenance.tool_manifest_hash
        AEGIS_CONTAINER_IMAGE_DIGEST  → provenance.container_image_digest
        AEGIS_SBOM_REF                → provenance.sbom_ref

    Missing / empty → `None`. Same profile hash whether the env-var is
    absent or set to the empty string.
    """
    return ProfileProvenance(
        model_ref=_read("AEGIS_MODEL_REF"),
        prompt_template_hash=_read("AEGIS_PROMPT_TEMPLATE_HASH"),
        tool_manifest_hash=_read("AEGIS_TOOL_MANIFEST_HASH"),
        container_image_digest=_read("AEGIS_CONTAINER_IMAGE_DIGEST"),
        sbom_ref=_read("AEGIS_SBOM_REF"),
    )
