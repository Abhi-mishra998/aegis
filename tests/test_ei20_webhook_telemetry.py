"""Sprint EI-20 — unit tests for webhook deliverability telemetry.

Covers:
  - WEBHOOK_STATUS_VOCAB content + lockstep with what the handlers
    actually emit (no drift between vocab + emissions)
  - _touch_webhook_telemetry helper signature
  - public_dict surfaces last_webhook_received_at + last_webhook_status
    on both vendors, with correct null-vs-iso handling, never the secret
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("INTERNAL_SECRET", "ei20-unit-test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.gateway.routers.integrations import (  # noqa: E402
    _snow_to_public_dict,
    _to_public_dict,
)
from services.gateway.routers.itsm_webhooks import (  # noqa: E402
    WEBHOOK_STATUS_VOCAB,
    _touch_webhook_telemetry,
)


# ── WEBHOOK_STATUS_VOCAB content ─────────────────────────────────────────
class TestVocab:
    EXPECTED = {
        "closed", "already_closed", "ignored",
        "unknown_issue_key", "unknown_sys_id",
        "no_issue_key", "no_sys_id",
        "bad_signature", "patch_failed",
    }   # intentionally NO `no_config` — see WEBHOOK_STATUS_VOCAB docstring

    def test_vocab_matches_expected(self):
        assert set(WEBHOOK_STATUS_VOCAB) == self.EXPECTED

    def test_vocab_is_frozen(self):
        with pytest.raises(AttributeError):
            WEBHOOK_STATUS_VOCAB.add("anything")   # frozenset → no .add

    def test_lockstep_with_jira_handler_emissions(self):
        """The Jira handler emits exactly these status words from
        _touch_webhook_telemetry calls. If a new code path adds an emit
        with a value not in WEBHOOK_STATUS_VOCAB, this test breaks."""
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] /
               "services" / "gateway" / "routers" / "itsm_webhooks.py").read_text()
        # Capture every literal that lands as the 4th arg to
        # _touch_webhook_telemetry(db, model, tenant_id, "<status>").
        emits = set(re.findall(
            r'_touch_webhook_telemetry\([^,]+,[^,]+,[^,]+,\s*["\']([a-z_]+)["\']\s*\)',
            src,
        ))
        # Some emissions use a computed `final_status` variable — skip
        # those (the values are inferred from a different code path).
        # Everything literal should be in the vocab.
        for word in emits:
            assert word in WEBHOOK_STATUS_VOCAB, (
                f"handler emits {word!r} but it's not in WEBHOOK_STATUS_VOCAB"
            )

    def test_no_orphan_vocab_entries_for_jira(self):
        """Every vocab entry should be emitted somewhere — no dead
        entries in the vocab. SNOW-specific words (unknown_sys_id,
        no_sys_id) emitted only on the SNOW path; the regex above
        catches both paths."""
        import re
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1] /
               "services" / "gateway" / "routers" / "itsm_webhooks.py").read_text()
        emits = set(re.findall(
            r'_touch_webhook_telemetry\([^,]+,[^,]+,[^,]+,\s*["\']([a-z_]+)["\']\s*\)',
            src,
        )) | {"closed", "patch_failed"}  # emitted via final_status variable
        # Every vocab entry should be in the union of emissions.
        for word in WEBHOOK_STATUS_VOCAB:
            assert word in emits, f"vocab entry {word!r} is never emitted"


# ── _touch_webhook_telemetry signature ──────────────────────────────────
class TestTouchSignature:
    def test_helper_is_async(self):
        import inspect
        assert inspect.iscoroutinefunction(_touch_webhook_telemetry)

    def test_helper_accepts_4_positional(self):
        import inspect
        sig = inspect.signature(_touch_webhook_telemetry)
        # db, model_cls, tenant_id, status_word
        assert list(sig.parameters)[:4] == ["db", "model_cls", "tenant_id", "status_word"]


# ── public_dict shape (Jira + SNOW) ─────────────────────────────────────
class TestPublicDictTelemetry:
    def _jira_row(self, **overrides):
        defaults = dict(
            id=uuid.uuid4(),
            base_url="https://acme.atlassian.net",
            project_key="SEC",
            account_email="bot@acme.com",
            api_token="secret-token",
            default_issue_type="Bug",
            default_priority="High",
            enabled=True,
            auto_create_on_incident=True,
            webhook_secret=None,
            last_webhook_received_at=None,
            last_webhook_status=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _snow_row(self, **overrides):
        defaults = dict(
            id=uuid.uuid4(),
            instance_url="https://example.com",
            username="aegis_bot",
            password="topsecret",
            default_urgency=2,
            default_impact=2,
            default_category=None,
            default_assignment_group=None,
            enabled=True,
            auto_create_on_incident=True,
            webhook_secret=None,
            last_webhook_received_at=None,
            last_webhook_status=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_jira_telemetry_fields_present_even_when_null(self):
        d = _to_public_dict(self._jira_row())
        assert "last_webhook_received_at" in d
        assert "last_webhook_status" in d
        assert d["last_webhook_received_at"] is None
        assert d["last_webhook_status"] is None

    def test_jira_telemetry_iso_when_set(self):
        ts = datetime.now(UTC)
        d = _to_public_dict(self._jira_row(
            last_webhook_received_at=ts,
            last_webhook_status="closed",
        ))
        assert d["last_webhook_received_at"] == ts.isoformat()
        assert d["last_webhook_status"] == "closed"

    def test_snow_telemetry_fields_present_even_when_null(self):
        d = _snow_to_public_dict(self._snow_row())
        assert d["last_webhook_received_at"] is None
        assert d["last_webhook_status"] is None

    def test_snow_telemetry_bad_signature_status(self):
        ts = datetime.now(UTC)
        d = _snow_to_public_dict(self._snow_row(
            last_webhook_received_at=ts,
            last_webhook_status="bad_signature",
        ))
        assert d["last_webhook_status"] == "bad_signature"

    def test_jira_secret_still_not_leaked(self):
        """EI-18 + EI-20 should both hold — no value of webhook_secret
        ever returned."""
        d = _to_public_dict(self._jira_row(webhook_secret="x" * 64,
                                            last_webhook_status="closed"))
        assert "webhook_secret" not in d
        assert d["has_webhook_secret"] is True

    def test_snow_secret_still_not_leaked(self):
        d = _snow_to_public_dict(self._snow_row(webhook_secret="y" * 64,
                                                  last_webhook_status="closed"))
        assert "webhook_secret" not in d
        assert d["has_webhook_secret"] is True
