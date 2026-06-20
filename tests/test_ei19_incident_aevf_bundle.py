"""Sprint EI-19 — unit tests for per-incident AEVF bundle wiring.

Covers the pieces that are testable without Postgres:
  - generate_verifiable_bundle accepts audit_ids and the parameter
    shape is preserved (existing 5-arg callers still work)
  - _IncidentBundleRequest schema validation (audit_ids cap, framework
    enum, optional incident_number)
  - RBAC matrix for /incidents/{id}/aevf-bundle (SECURITY_ANALYST+)
"""
from __future__ import annotations

import inspect
import os
import sys
import uuid
from pathlib import Path

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei19-unit-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.gateway._rbac_map import is_authorized  # noqa: E402


# ── generate_verifiable_bundle signature ────────────────────────────────
class TestGeneratorSignature:
    """Verify the EI-19 extension didn't break the existing 5-arg shape
    and that audit_ids landed as a keyword-only optional."""

    def test_audit_ids_is_keyword_only_optional(self):
        from services.audit.verifiable_bundle import generate_verifiable_bundle
        sig = inspect.signature(generate_verifiable_bundle)
        assert "audit_ids" in sig.parameters
        p = sig.parameters["audit_ids"]
        assert p.default is None
        assert p.kind == inspect.Parameter.KEYWORD_ONLY

    def test_legacy_5_arg_call_shape_preserved(self):
        """The existing /verifiable-bundle/{framework} handler calls
        with positional (db, tenant_id, framework, period_start,
        period_end). That must still work."""
        from services.audit.verifiable_bundle import generate_verifiable_bundle
        sig = inspect.signature(generate_verifiable_bundle)
        positional = [
            n for n, p in sig.parameters.items()
            if p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            )
        ]
        # Old shape: db, tenant_id, framework, period_start, period_end
        assert positional[:5] == [
            "db", "tenant_id", "framework", "period_start", "period_end",
        ]


# ── _IncidentBundleRequest schema ───────────────────────────────────────
class TestBundleRequestSchema:
    def _cls(self):
        from services.audit.compliance import _IncidentBundleRequest
        return _IncidentBundleRequest

    def test_empty_audit_ids_accepted(self):
        cls = self._cls()
        req = cls(audit_ids=[])
        assert req.audit_ids == []
        assert req.framework == "eu-ai-act"   # default
        assert req.incident_number is None

    def test_single_uuid_accepted(self):
        cls = self._cls()
        uid = uuid.uuid4()
        req = cls(audit_ids=[uid])
        assert req.audit_ids == [uid]

    def test_max_length_enforced(self):
        cls = self._cls()
        too_many = [uuid.uuid4() for _ in range(10_001)]
        with pytest.raises(Exception):   # pydantic ValidationError
            cls(audit_ids=too_many)

    def test_framework_default_is_eu_ai_act(self):
        cls = self._cls()
        req = cls(audit_ids=[])
        assert req.framework == "eu-ai-act"

    def test_incident_number_propagates(self):
        cls = self._cls()
        req = cls(audit_ids=[], incident_number="INC-2026-0042")
        assert req.incident_number == "INC-2026-0042"

    def test_garbage_uuid_rejected(self):
        cls = self._cls()
        with pytest.raises(Exception):
            cls(audit_ids=["not-a-uuid"])


# ── RBAC matrix for /incidents/{id}/aevf-bundle ─────────────────────────
class TestRBAC:
    @pytest.mark.parametrize("role,allowed", [
        ("OWNER",            True),
        ("ADMIN",            True),
        ("SECURITY_ANALYST", True),
        ("DEVELOPER",        False),
        ("READ_ONLY",        False),
    ])
    def test_security_analyst_min(self, role, allowed):
        ok, _ = is_authorized(
            "/incidents/abc-123/aevf-bundle", "GET", role,
        )
        assert ok is allowed

    def test_not_allowed_on_post(self):
        """The endpoint is GET-only; POST must not be matched by the
        same rule and should fall through to /incidents/* (PATCH/POST/
        DELETE → SECURITY_ANALYST). Verify it doesn't grant something
        we didn't intend."""
        ok, _ = is_authorized(
            "/incidents/abc-123/aevf-bundle", "POST", "DEVELOPER",
        )
        # DEVELOPER on POST should fail — /incidents/* PATCH/POST/DELETE
        # is SECURITY_ANALYST+; DEVELOPER < SECURITY_ANALYST.
        assert ok is False

    def test_specificity_over_catch_all(self):
        """The /incidents/*/aevf-bundle rule must take precedence over
        the catch-all /incidents* GET rule (READ_ONLY) — otherwise a
        READ_ONLY user could download AEVF bundles. Verify by checking
        READ_ONLY is rejected on the specific endpoint."""
        ok, _ = is_authorized(
            "/incidents/abc-123/aevf-bundle", "GET", "READ_ONLY",
        )
        assert ok is False


# ── EI-13 + EI-15 regression — ensure nothing in this sprint broke
# the prior SBOM CVE pipeline.
class TestNoRegression:
    def test_sbom_cve_diff_module_still_imports(self):
        # Pulls in scripts/ops/sbom_cve_diff.py via the existing test
        # import path; just check the module loads.
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
        import sbom_cve_diff  # noqa: F401

    def test_uptime_rollup_module_still_imports(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "ops"))
        import uptime_rollup  # noqa: F401
