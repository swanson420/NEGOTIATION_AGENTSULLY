"""
Unified Ledger Protocol Adapter (Route-Normalized & Fail-Closed).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class LedgerWriteError(RuntimeError):
    """Raised when ledger.record_decision_view returns False or throws."""
    pass


class LedgerContractError(RuntimeError):
    """Raised when the ledger client fails structural protocol validation."""
    pass


class LedgerAuditEnvelope(BaseModel):
    """
    Standardized payload conforming to ledger._normalize_record.
    `route` is Optional: populated ONLY when a genuine RouteType trajectory exists.
    """
    audit_hash: str
    route: Optional[str] = None
    view_name: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: Dict[str, Any] = Field(default_factory=dict)

    def to_ledger_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "audit_hash": self.audit_hash,
            "view_name": self.view_name,
            "timestamp": self.timestamp.isoformat(),
            **self.data
        }
        if self.route is not None:
            payload["route"] = self.route
        return payload


def emit_ledger_view(
    ledger_client: Any,
    audit_hash: str,
    view_name: str,
    view_data: Dict[str, Any],
    route: Optional[str] = None,
) -> None:
    """
    Universal, fail-closed write helper for ledger/ledger.py.
    """
    if not hasattr(ledger_client, "record_decision_view") or not callable(ledger_client.record_decision_view):
        raise LedgerContractError("Ledger client lacks callable 'record_decision_view(record)'.")

    envelope = LedgerAuditEnvelope(
        audit_hash=audit_hash,
        route=route,
        view_name=view_name,
        data=view_data
    )

    try:
        success = ledger_client.record_decision_view(envelope.to_ledger_payload())
    except Exception as exc:
        raise LedgerWriteError(
            f"Ledger raised unexpected exception for view '{view_name}' [audit_hash={audit_hash}]: {str(exc)}"
        ) from exc

    if not success:
        raise LedgerWriteError(
            f"Ledger rejected audit write for view '{view_name}' [audit_hash={audit_hash}]. Returned False."
        )
