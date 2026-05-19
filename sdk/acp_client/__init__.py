"""
ACP — Tamper-evident replay + runtime deny for AI agents.

Customer-facing SDK. Wraps the ACP gateway so an agent author can:

    from sdk.acp_client import Client

    acp = Client(api_key="...", base_url="https://acp.example.com")

    @acp.protect(agent_id="agent_42")
    def my_agent(prompt: str) -> str:
        ...

Every call to `my_agent(...)` is routed through ACP: policy check before
execution, audit log + signed receipt after. If the policy denies, the call
raises `acp.DeniedError` and never reaches your function.
"""
from .client import Client
from .errors import (
    ACPError,
    DecisionTimeoutError,
    DeniedError,
    EscalationRequiredError,
    PolicyError,
    RateLimitedError,
)
from .findings import CANONICAL_FINDINGS, FINDINGS
from .policy import Policy, load_policy, validate_policy
from .receipts import verify_receipt
from .transparency import (
    leaf_hash_for_receipt,
    verify_inclusion,
    verify_root_chain,
    verify_root_signature,
)

__all__ = [
    "Client",
    "Policy",
    "load_policy",
    "validate_policy",
    "verify_receipt",
    "verify_inclusion",
    "verify_root_chain",
    "verify_root_signature",
    "leaf_hash_for_receipt",
    "ACPError",
    "DecisionTimeoutError",
    "DeniedError",
    "EscalationRequiredError",
    "PolicyError",
    "RateLimitedError",
    "CANONICAL_FINDINGS",
    "FINDINGS",
]

__version__ = "0.2.0"
