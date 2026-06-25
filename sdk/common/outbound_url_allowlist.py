"""Sprint 25 B1 — outbound-URL allowlist for tenant-configured destinations.

Blocks the SSRF class where a tenant OWNER sets an integration's base_url or
webhook_url to an internal target (AWS metadata, localhost, private CIDR) and
the platform happily delivers payloads — leaking IAM creds, internal services,
or routing data into private networks.

Public entry: ``validate_outbound_url(url, *, allowed_schemes=("https",)) -> None``
Raises ``OutboundUrlBlocked`` on any rejection. Caller catches and returns 400.

The validation is PUT-time (when a tenant saves an integration). Request-time
re-validation would close the DNS-rebinding window but adds latency to every
webhook delivery; that's deferred to a follow-up if a real exploit is shown.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

__all__ = ["OutboundUrlBlocked", "validate_outbound_url"]


class OutboundUrlBlocked(ValueError):
    """Outbound URL violated the allowlist. ``reason`` is a stable code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


# Default — HTTPS only. Caller can opt in to "http" for local-dev integrations
# but never gets to bypass the IP check.
_DEFAULT_SCHEMES: tuple[str, ...] = ("https",)
_ALLOWED_PORTS: frozenset[int] = frozenset({80, 443, 8080, 8443})


def _is_blocked_ip(ip_str: str) -> tuple[bool, str]:
    """Return (blocked, reason). True for any non-globally-routable address.

    Order matters: the more-specific labels (loopback, link_local) win over
    the general `private_cidr` so operators see "link_local" for the AWS
    metadata IP instead of the vaguer "private_cidr".
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, "invalid_ip"
    if ip.is_loopback:
        return True, "loopback"
    if ip.is_link_local:  # 169.254.x.x and fe80::/10 — AWS/GCP metadata + IPv6 LL
        return True, "link_local"
    if ip.is_multicast:
        return True, "multicast"
    if ip.is_unspecified:
        return True, "unspecified"
    if ip.is_reserved:
        return True, "reserved"
    if ip.is_private:
        return True, "private_cidr"
    return False, ""


def validate_outbound_url(
    url: str,
    *,
    allowed_schemes: tuple[str, ...] = _DEFAULT_SCHEMES,
) -> None:
    """Raise ``OutboundUrlBlocked`` if the URL is unsafe for outbound delivery.

    Checks:
      1. Parses as a URL with a scheme + host.
      2. Scheme is in ``allowed_schemes`` (default: HTTPS only).
      3. Port (if explicit) is in {80, 443, 8080, 8443}.
      4. Host resolves to a publicly-routable IP (rejects private CIDRs,
         loopback, link-local, multicast, unspecified, reserved).

    Does NOT defend against DNS rebinding — that requires re-validating at
    request time. Deferred until a real exploit is demonstrated.
    """
    if not isinstance(url, str) or not url.strip():
        raise OutboundUrlBlocked("empty_url", "URL is empty")

    parsed = urlparse(url.strip())
    if parsed.scheme.lower() not in allowed_schemes:
        raise OutboundUrlBlocked(
            "scheme_blocked",
            f"scheme {parsed.scheme!r} not allowed (must be one of {sorted(allowed_schemes)})",
        )

    host = (parsed.hostname or "").strip()
    if not host:
        raise OutboundUrlBlocked("no_host", "URL has no host")

    port = parsed.port
    if port is not None and port not in _ALLOWED_PORTS:
        raise OutboundUrlBlocked(
            "port_blocked",
            f"port {port} not allowed (must be one of {sorted(_ALLOWED_PORTS)})",
        )

    # If the host is already an IP literal, check directly. Otherwise resolve.
    try:
        ipaddress.ip_address(host)
        ip_str = host
    except ValueError:
        try:
            ip_str = socket.gethostbyname(host)
        except socket.gaierror as exc:
            raise OutboundUrlBlocked(
                "dns_failed", f"DNS resolution failed for {host}: {exc}"
            ) from exc

    blocked, reason = _is_blocked_ip(ip_str)
    if blocked:
        raise OutboundUrlBlocked(
            reason,
            f"host {host} resolves to {ip_str} which is {reason.replace('_', ' ')}",
        )


if __name__ == "__main__":  # pragma: no cover — self-check
    # ponytail: keep the assert-based self-check next to the code.
    # Use IP literals so the self-check works offline (no DNS dependency).
    OK = [
        "https://8.8.8.8/webhook",       # public IP
        "https://1.1.1.1:8443/webhook",  # public IP + allowed port
        "https://[2606:4700:4700::1111]/",  # public IPv6
    ]
    BAD = [
        ("https://169.254.169.254/latest/meta-data/", "link_local"),  # AWS metadata
        ("https://127.0.0.1/", "loopback"),
        ("https://10.0.0.1/internal", "private_cidr"),
        ("https://192.168.1.1/", "private_cidr"),
        ("https://172.16.5.5/", "private_cidr"),
        ("https://[::1]/", "loopback"),
        ("https://[fe80::1]/", "link_local"),
        ("http://169.254.169.254/latest/meta-data/", "scheme_blocked"),  # scheme check first
        ("file:///etc/passwd", "scheme_blocked"),
        ("ftp://example.com/", "scheme_blocked"),
        ("https://1.1.1.1:22/", "port_blocked"),  # SSH port blocked
        ("", "empty_url"),
        ("https://", "no_host"),
    ]
    for u in OK:
        validate_outbound_url(u)
    for u, expected in BAD:
        try:
            validate_outbound_url(u)
        except OutboundUrlBlocked as e:
            assert e.reason == expected, f"{u}: expected {expected}, got {e.reason}"
        else:
            raise AssertionError(f"{u!r} should have been blocked as {expected}")
    print(f"OK: {len(OK)} allowed, {len(BAD)} blocked, all reasons match")
