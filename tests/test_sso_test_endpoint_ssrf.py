"""Regression: /auth/sso/config/test must refuse SSRF-shaped issuers.

An authenticated tenant caller can otherwise POST an issuer of
`http://169.254.169.254/latest/meta-data/iam/security-credentials/...`
and the identity worker will fetch it — leaking IAM creds into the
`.issuer` claim of the response (or into the redirect chain if
`follow_redirects=True`).

We test the SSRF guard directly (imported from sdk.common) rather than
spinning up the identity FastAPI app + Postgres, because the guard IS
the security boundary and locking in the guard's behavior against known
attack shapes is the durable regression.
"""
from __future__ import annotations

import pytest

from sdk.common.outbound_url_allowlist import (
    OutboundUrlBlocked,
    validate_outbound_url,
)


class TestSsoTestUrlSsrfGuard:
    def test_aws_metadata_ipv4_blocked(self):
        with pytest.raises(OutboundUrlBlocked) as exc:
            validate_outbound_url(
                "http://169.254.169.254/latest/meta-data/",
                allowed_schemes=("http", "https"),
            )
        assert exc.value.reason == "link_local"

    def test_localhost_blocked(self):
        with pytest.raises(OutboundUrlBlocked) as exc:
            validate_outbound_url(
                "http://127.0.0.1/.well-known/openid-configuration",
                allowed_schemes=("http", "https"),
            )
        assert exc.value.reason == "loopback"

    def test_private_cidr_blocked(self):
        with pytest.raises(OutboundUrlBlocked) as exc:
            validate_outbound_url(
                "https://10.0.0.5/.well-known/openid-configuration",
                allowed_schemes=("http", "https"),
            )
        assert exc.value.reason == "private_cidr"

    def test_ipv6_localhost_blocked(self):
        with pytest.raises(OutboundUrlBlocked) as exc:
            validate_outbound_url(
                "https://[::1]/.well-known/openid-configuration",
                allowed_schemes=("http", "https"),
            )
        assert exc.value.reason == "loopback"

    def test_file_scheme_blocked(self):
        with pytest.raises(OutboundUrlBlocked) as exc:
            validate_outbound_url(
                "file:///etc/passwd",
                allowed_schemes=("http", "https"),
            )
        assert exc.value.reason == "scheme_blocked"

    def test_public_https_ip_allowed(self):
        # Sanity: legitimate IdPs (e.g. Cloudflare Access, Okta) at
        # public IPs must still pass the guard.
        validate_outbound_url(
            "https://1.1.1.1/.well-known/openid-configuration",
            allowed_schemes=("http", "https"),
        )
