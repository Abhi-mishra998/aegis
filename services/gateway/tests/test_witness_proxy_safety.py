"""Path-param safety for the witness_proxy heartbeat route.

The `{witness_id:path}` parameter accepts everything until the query
string. A hostile caller with an internal mesh JWT could try to inject
path traversal (`../../admin`), query smuggling (`x?admin=1`), or
fragment smuggling into the downstream URL. All three are refused as
400 BEFORE any HTTP call is made — matches the SCIM client's
defense-in-depth pattern (`_is_safe_scim_id`).
"""
from __future__ import annotations

from services.gateway.routers.witness_proxy import _is_safe_witness_id


class TestIsSafeWitnessId:
    def test_alnum_id_ok(self):
        assert _is_safe_witness_id("witness-01")
        assert _is_safe_witness_id("w_1")

    def test_spiffe_uri_ok(self):
        assert _is_safe_witness_id("spiffe://acme.example/witness/node-7")

    def test_fqdn_dots_ok(self):
        assert _is_safe_witness_id("spiffe://acme.example.com/witness/x")

    def test_path_traversal_rejected(self):
        assert not _is_safe_witness_id("../../admin")
        assert not _is_safe_witness_id("spiffe://acme/witness/../../admin")
        assert not _is_safe_witness_id("..")
        assert not _is_safe_witness_id("witness/..")

    def test_single_dot_ok_but_double_dot_not(self):
        """Single `.` is legitimate (SPIFFE FQDNs). `..` is traversal."""
        assert _is_safe_witness_id("witness.node.1")
        assert not _is_safe_witness_id("witness..node")

    def test_query_string_rejected(self):
        assert not _is_safe_witness_id("witness?admin=1")

    def test_fragment_rejected(self):
        assert not _is_safe_witness_id("witness#anchor")

    def test_whitespace_rejected(self):
        assert not _is_safe_witness_id("witness one")
        assert not _is_safe_witness_id("witness\n")

    def test_ampersand_rejected(self):
        assert not _is_safe_witness_id("a&b=c")

    def test_unicode_rejected(self):
        assert not _is_safe_witness_id("witness-é")

    def test_empty_rejected(self):
        assert not _is_safe_witness_id("")

    def test_too_long_rejected(self):
        assert not _is_safe_witness_id("a" * 257)

    def test_at_symbol_rejected(self):
        """`@` in a witness id could look like userinfo when URL-parsed."""
        assert not _is_safe_witness_id("user@host")
