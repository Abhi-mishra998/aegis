"""Tests for the Anthropic pre-classifier used by the C3 sampling wire."""
from __future__ import annotations

import json

from services.gateway.routers.messages import (
    _classify_incoming_anthropic_request,
)


def _body(**kw) -> bytes:
    return json.dumps(kw).encode()


class TestPreClassify:
    def test_no_tools_returns_c2(self):
        assert _classify_incoming_anthropic_request(_body(model="claude-3")) == "C2"

    def test_read_only_tools_return_c2(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "get_user"}, {"name": "list_records"}],
        )) == "C2"

    def test_payment_tool_returns_c3(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "send_payment"}],
        )) == "C3"

    def test_transfer_tool_returns_c3(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "wire_transfer_funds"}],
        )) == "C3"

    def test_delete_tool_returns_c3(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "delete_customer_record"}],
        )) == "C3"

    def test_destructive_kubectl_returns_c3(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "kubectl_delete_namespace"}],
        )) == "C3"

    def test_case_insensitive(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "SEND_PAYMENT"}],
        )) == "C3"

    def test_mixed_tools_c3_wins(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "get_user"}, {"name": "send_payment"}],
        )) == "C3"

    def test_malformed_body_defaults_c2(self):
        # Not JSON → C2 (classification is a hint, not a security boundary)
        assert _classify_incoming_anthropic_request(b"not-json") == "C2"

    def test_non_dict_body_defaults_c2(self):
        assert _classify_incoming_anthropic_request(_body().replace(
            b"{}", b"[]"
        )) == "C2"

    def test_tools_not_list_defaults_c2(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools="not-a-list",
        )) == "C2"

    def test_tool_not_dict_skipped(self):
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=["send_payment", {"name": "send_payment"}],
        )) == "C3"  # dict entry still matches

    # ─────────────────────────────────────────────────────────────
    # Token-boundary regression tests — substring false positives
    # would silently over-classify read-only tools as C3, exhausting
    # the sampling budget on innocent calls.
    # ─────────────────────────────────────────────────────────────

    def test_get_pay_history_is_read_not_c3(self):
        """`get_pay_history` READS payment records — not a payment
        action. Substring match would false-positive on 'pay'."""
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "get_pay_history"}],
        )) == "C2"

    def test_undelete_backup_is_not_c3(self):
        """`undelete_backup` RESTORES data — not a delete. Substring
        match on 'delete' would false-positive."""
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "undelete_backup"}],
        )) == "C2"

    def test_dropbox_client_is_not_c3(self):
        """`dropbox_client` mentions 'drop' but is a filesystem client
        name, not a destructive verb."""
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "dropbox_client_list"}],
        )) == "C2"

    def test_terminate_prefix_still_matches(self):
        """`terminate_session` IS destructive — token boundary at
        underscore means the token itself is a distinct word."""
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "terminate_session"}],
        )) == "C3"

    def test_hyphenated_wire_transfer_matches(self):
        """`wire-transfer` uses hyphen as boundary — still C3."""
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "wire-transfer-funds"}],
        )) == "C3"

    def test_iac_destroy_multi_token_matches(self):
        """`iac_destroy` is itself a distinct token — matches whole."""
        assert _classify_incoming_anthropic_request(_body(
            model="claude-3",
            tools=[{"name": "iac_destroy"}],
        )) == "C3"
