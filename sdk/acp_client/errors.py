class ACPError(Exception):
    """Base class for all ACP SDK errors."""


class DeniedError(ACPError):
    """Raised when a policy denies an agent action.

    Attributes:
        reason: Short machine-readable reason (e.g. "tool_not_allowed").
        detail: Human-readable explanation from the policy engine.
        decision_id: ID of the decision record in the audit log.
    """

    def __init__(self, reason: str, detail: str, decision_id: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        self.decision_id = decision_id
        super().__init__(f"{reason}: {detail}")


class PolicyError(ACPError):
    """Raised when a local policy file is malformed."""


class RateLimitedError(ACPError):
    """Raised when the gateway returns 429."""

    def __init__(self, retry_after: float | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"rate limited; retry after {retry_after}s" if retry_after else "rate limited"
        )


class EscalationRequiredError(DeniedError):
    """Raised when an action requires out-of-band approval.

    The gateway returns 403 with `error: "approval_required"`. Callers
    that already catch `DeniedError` keep working unchanged (this is a
    subclass); callers that want to distinguish "policy denied this
    permanently" from "policy denied this pending approval" can catch
    `EscalationRequiredError` specifically.

    The approval workflow lives at /autonomy/overrides — once approved,
    retrying the same call typically succeeds with 200.
    """

    def __init__(self, detail: str, decision_id: str | None = None,
                 contract_id: str | None = None) -> None:
        self.contract_id = contract_id
        super().__init__(
            reason="approval_required",
            detail=detail,
            decision_id=decision_id,
        )


class DecisionTimeoutError(ACPError):
    """Raised when the gateway returns 504 because the decision pipeline
    exceeded the configured deadline.

    `/execute` is strictly synchronous (no 202 / no polling), so the
    correct semantic for "took too long" is a clean timeout. Retrying
    the call is usually safe; the gateway has already written a
    transparency-chain audit row for the timed-out attempt.
    """

    def __init__(self, detail: str, request_id: str | None = None) -> None:
        self.detail = detail
        self.request_id = request_id
        super().__init__(f"decision_timeout: {detail}")
