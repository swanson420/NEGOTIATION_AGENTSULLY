"""
Actuation State Reconciliation Loop (Isolated Feedback & Sweep Resilience).

Scans the Ledger for unacknowledged actuation intents (drift detection)
and executes automated compensating feedback or state correction without
allowing individual item persistence faults to truncate the sweep.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from action.commitment_gradient import classify_commitment_level
from action.dispatch import DispatchTarget, route_dispatch
from action.action_gate import ActionGateResult
from ledger.adapter import emit_ledger_view, LedgerContractError, LedgerWriteError


class StalledActuationRecord(BaseModel):
    """Represents a state divergence where an action was authorized but not confirmed."""
    audit_hash: str
    decision_record: Any
    target: DispatchTarget
    intent_timestamp: datetime
    elapsed_seconds: float


class ReconciliationReport(BaseModel):
    """Summary of reconciliation sweep with decoupled telemetry accounting."""
    scanned_count: int
    reconciled_count: int
    failed_count: int
    orphans: List[str] = Field(default_factory=list)
    audit_persistence_faults: List[str] = Field(
        default_factory=list,
        description="Audit hashes where the recovery status itself could not be persisted."
    )


class ActuationReconciler:
    """
    Closed-loop auditor detecting and resolving dropped actuator transitions
    with isolated per-item fault containment.
    """

    def __init__(self, ledger_client: Any, timeout_threshold_seconds: float = 30.0):
        if not hasattr(ledger_client, "record_decision_view") or not callable(ledger_client.record_decision_view):
            raise LedgerContractError("Ledger client must implement callable 'record_decision_view(record)'.")
        if not hasattr(ledger_client, "get_uncommitted_views") or not callable(ledger_client.get_uncommitted_views):
            raise LedgerContractError("Ledger client must implement callable 'get_uncommitted_views(view_name, older_than_seconds)'.")

        self._ledger = ledger_client
        self._timeout_seconds = timeout_threshold_seconds

    def scan_and_reconcile(self) -> ReconciliationReport:
        """
        Queries persistent ledger for unacknowledged intents and processes each
        under an isolated fault boundary so individual persistence or actuation
        failures never truncate the supervisory sweep.
        """
        pending_intents = self._fetch_uncommitted_intents()
        reconciled = 0
        failed = 0
        orphans: List[str] = []
        audit_faults: List[str] = []

        for item in pending_intents:
            try:
                self._reconcile_single_intent(item)
                reconciled += 1
            except Exception as exc:
                failed += 1
                orphans.append(item.audit_hash)

                try:
                    self._record_reconciliation_audit(
                        audit_hash=item.audit_hash,
                        status="reconciliation_fault",
                        extra_data={
                            "error": str(exc),
                            "timestamp": datetime.now(timezone.utc).isoformat()
                        },
                        route=None
                    )
                except Exception as audit_exc:
                    audit_faults.append(
                        f"audit_hash={item.audit_hash} | primary_error={str(exc)} | audit_error={str(audit_exc)}"
                    )

        return ReconciliationReport(
            scanned_count=len(pending_intents),
            reconciled_count=reconciled,
            failed_count=failed,
            orphans=orphans,
            audit_persistence_faults=audit_faults
        )

    def _fetch_uncommitted_intents(self) -> List[StalledActuationRecord]:
        """Fetches pending intent views; raises LedgerContractError if query fails."""
        try:
            raw_entries = self._ledger.get_uncommitted_views(
                view_name="action_gate_evaluation",
                older_than_seconds=self._timeout_seconds
            )
        except Exception as exc:
            raise LedgerContractError(f"Failed to query uncommitted views from ledger: {str(exc)}") from exc

        return [
            StalledActuationRecord(
                audit_hash=entry["audit_hash"],
                decision_record=entry["decision_record"],
                target=DispatchTarget(entry["dispatch_target"]),
                intent_timestamp=entry["timestamp"],
                elapsed_seconds=entry["elapsed_seconds"]
            )
            for entry in raw_entries
        ]

    def _reconcile_single_intent(self, item: StalledActuationRecord) -> None:
        """Attempts deterministic re-actuation or records non-idempotent hold."""
        context = getattr(item.decision_record, "context", {}) or {}
        action_name = getattr(item.decision_record, "action", "unknown_action")
        is_idempotent = context.get("idempotent", False)

        if not is_idempotent:
            self._record_reconciliation_audit(
                audit_hash=item.audit_hash,
                status="reconciliation_aborted_non_idempotent",
                extra_data={
                    "reason": f"State unconfirmed after {item.elapsed_seconds:.1f}s. Duplicate execution prevented."
                },
                route=None
            )
            return

        commitment_eval = classify_commitment_level(action_name, context)
        reconstructed_gate_result = ActionGateResult(
            permitted=True,
            audit_hash=item.audit_hash,
            commitment_level=commitment_eval.level,
            dispatch_target=item.target,
            rejection_reason=None,
            telemetry={
                "reversibility_half_life": commitment_eval.reversibility_half_life_seconds,
                "invariants": commitment_eval.invariants_checked,
                "reconciled_replay": True,
            }
        )

        receipt = route_dispatch(
            target=item.target,
            decision_record=item.decision_record,
            gate_result=reconstructed_gate_result
        )

        self._record_reconciliation_audit(
            audit_hash=item.audit_hash,
            status="actuation_reconciled",
            extra_data={
                "handler_executed": receipt.handler_executed,
                "reconciliation_timestamp": datetime.now(timezone.utc).isoformat()
            },
            route=None
        )

    def _record_reconciliation_audit(
        self,
        audit_hash: str,
        status: str,
        extra_data: dict,
        route: Optional[str] = None
    ) -> None:
        payload = {"status": status, **extra_data}
        emit_ledger_view(
            ledger_client=self._ledger,
            audit_hash=audit_hash,
            view_name="action_gate_evaluation",
            view_data=payload,
            route=route
        )
