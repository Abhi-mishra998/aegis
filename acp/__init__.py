"""
acp — Tamper-evident replay + runtime deny for AI agents.

Public Python SDK. Five-line integration:

    import acp

    client = acp.Client(api_key="...", base_url="https://acp.example.com")

    @client.protect(agent_id="agent_42")
    def my_agent(prompt: str) -> str:
        ...

This module is a thin namespace alias over `sdk.acp_client` — the canonical
package location inside this repo. Every public symbol surfaces here so
customers never have to know the internal layout.
"""
from sdk.acp_client import (
    ACPError,
    Client,
    DeniedError,
    Policy,
    PolicyError,
    RateLimitedError,
    leaf_hash_for_receipt,
    load_policy,
    validate_policy,
    verify_inclusion,
    verify_receipt,
)
from sdk.acp_client import __version__ as _sdk_version

__version__ = _sdk_version
VERSION = _sdk_version

__all__ = [
    "Client",
    "Policy",
    "load_policy",
    "validate_policy",
    "verify_receipt",
    "verify_inclusion",
    "leaf_hash_for_receipt",
    "ACPError",
    "DeniedError",
    "PolicyError",
    "RateLimitedError",
    "VERSION",
    "__version__",
]
