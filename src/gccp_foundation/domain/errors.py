from dataclasses import asdict, dataclass
from enum import StrEnum


class ErrorCategory(StrEnum):
    ENVIRONMENT = "environment"
    BINDING = "binding"
    VERSION = "version"
    PLAN = "plan"
    IDENTITY = "identity"
    OPERATION = "operation"
    VERIFICATION = "verification"
    STORAGE = "storage"
    INTERNAL = "internal"


class DispatchState(StrEnum):
    NONE = "none"
    SENT = "sent"
    UNKNOWN = "outcome_unknown"


class Recovery(StrEnum):
    READ_ONLY_RETRY = "read_only_retry"
    REBIND = "rebind"
    RECONCILE = "reconcile"
    BUSINESS_DECISION = "business_decision"
    REPAIR = "repair"


@dataclass(frozen=True)
class ErrorDetail:
    code: str
    category: ErrorCategory
    message: str
    dispatch_state: DispatchState = DispatchState.NONE
    recovery: Recovery = Recovery.REPAIR
    retry_allowed: bool = False

    def to_dict(self) -> dict:
        return {key: str(value) if isinstance(value, StrEnum) else value
                for key, value in asdict(self).items()}


class FoundationError(ValueError):
    def __init__(self, detail: ErrorDetail):
        self.detail = detail
        super().__init__(detail.message)


class ContractError(FoundationError):
    def __init__(self, message: str, code: str = "INVALID_PLAN"):
        super().__init__(ErrorDetail(code, ErrorCategory.PLAN, message))


class ResourceSpecNotSent(FoundationError):
    """Raised only by a live resource guard before its first field attempt."""
    def __init__(self, message):
        super().__init__(ErrorDetail('RESOURCE_SPEC_NOT_SENT', ErrorCategory.OPERATION,
                                    message, dispatch_state=DispatchState.NONE))
