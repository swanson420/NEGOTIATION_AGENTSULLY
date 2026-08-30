"""
Dispatch Router (Closed-Loop Actuation).

Directs DecisionRecords to one of five discrete dispatch targets based
on Action Gate adjudication. Fails closed with explicit faults if a
target handler is missing or throws an exception.
"""

from enum import Enum
from typing import Any, Callable, Dict, Optional
from pydantic import BaseModel


class DispatchTarget(str, Enum):
    """Supported actuation and queueing targets."""
    RESEARCH = "research"
    DRAFT = "draft"
    SEND = "send"
    WAIT = "wait"
    BOUNCE = "bounce"


class DispatchExecutionError(RuntimeError):
    """Raised when an actuation cannot be dispatched or its handler fails."""
    def __init__(self, target: DispatchTarget, audit_hash: Optional[str], reason: str):
        super().__init__(f"Dispatch failure for target '{target.value}' [audit_hash={audit_hash}]: {reason}")
        self.target = target
        self.audit_hash = audit_hash
        self.reason = reason


class DispatchReceipt(BaseModel):
    """Closed-loop confirmation of successful dispatch routing."""
    target: DispatchTarget
    audit_hash: str
    handler_executed: str
    execution_result: Optional[Any] = None


_DISPATCH_HANDLERS: Dict[DispatchTarget, Callable[[Any, Any], Any]] = {}


def register_handler(target: DispatchTarget, handler: Callable[[Any, Any], Any]) -> None:
    """Registers an execution handler for a specific dispatch target."""
    _DISPATCH_HANDLERS[target] = handler


def clear_handlers() -> None:
    """Resets all registered handlers (utility for test isolation)."""
    _DISPATCH_HANDLERS.clear()


def route_dispatch(target: DispatchTarget, decision_record: Any, gate_result: Any) -> DispatchReceipt:
    """
    Terminal dispatch routing with closed-loop verification.
    """
    audit_hash = getattr(gate_result, "audit_hash", None) or getattr(decision_record, "audit_hash", "UNKNOWN_HASH")
    handler = _DISPATCH_HANDLERS.get(target)

    if handler is None:
        raise DispatchExecutionError(
            target=target,
            audit_hash=audit_hash,
            reason=f"No actuator handler registered for target '{target.value}'."
        )

    try:
        result = handler(decision_record, gate_result)
    except Exception as exc:
        raise DispatchExecutionError(
            target=target,
            audit_hash=audit_hash,
            reason=f"Actuator handler raised an unhandled exception: {str(exc)}"
        ) from exc

    return DispatchReceipt(
        target=target,
        audit_hash=audit_hash,
        handler_executed=getattr(handler, "__name__", str(handler)),
        execution_result=result
    )
