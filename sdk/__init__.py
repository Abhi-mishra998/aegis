# `sdk` is a namespace package. The legacy ACPClient and common exceptions
# below are exposed for source-tree usage (tests, internal services); the
# imports are lazy/tolerant so the published customer SDK wheel — which ships
# only `sdk.acp_client` — can still `import sdk` without error.

__all__: list[str] = []

try:
    from sdk.client import ACPClient as _ACPClient  # type: ignore[attr-defined]
    from sdk.common.exceptions import (  # type: ignore[attr-defined]
        ACPAuthError as _ACPAuthError,
    )
    from sdk.common.exceptions import (
        ACPConnectionError as _ACPConnectionError,
    )
    from sdk.common.exceptions import (
        ACPError as _ACPError,
    )
    from sdk.common.exceptions import (
        ACPPolicyDeniedError as _ACPPolicyDeniedError,
    )

    ACPClient = _ACPClient
    ACPError = _ACPError
    ACPAuthError = _ACPAuthError
    ACPPolicyDeniedError = _ACPPolicyDeniedError
    ACPConnectionError = _ACPConnectionError

    __all__ = [
        "ACPClient",
        "ACPError",
        "ACPAuthError",
        "ACPPolicyDeniedError",
        "ACPConnectionError",
    ]
except ImportError:
    # Slim SDK wheel install — legacy modules are intentionally absent.
    pass
