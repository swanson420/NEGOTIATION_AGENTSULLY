"""
Action Gate Convergence Point (Precondition-Enforced, Hash-Verified).
"""

from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from decision.contracts.adversarial_clearance import AdversarialClearance, AdversarialClearanceStatus
from action.commitment_gradient import CommitmentLevel, CommitmentEvaluation, classify_commitment_level
from action.dispatch import DispatchTarget, DispatchReceipt, DispatchExecutionError, route_dispatch
from ledger.adapter import emit_ledger_view, LedgerWriteError, LedgerContractError


class AdversarialPreconditionError(RuntimeError):
    """Raised when a DecisionRecord attempts to enter ActionGate without valid adversarial clearance."""
    pass


class ActionGateResult(BaseModel):
    """Result returned by the Action Gate prior to execution."""
    permitted: bool
    audit_hash: str
    commitment_level: CommitmentLevel
    dispatch_target: DispatchTarget
    rejection_reason: Optional[str] = None
    dispatch_receipt: Optional[DispatchReceipt] = None
    telemetry: Dict[str, Any] = Field(default_factory=dict)


class ActionGate:
    """
    Convergence gate enforcing commitment threshold policies, structural upstream
    precondition verification (including cryptographic hash re-verification),
    and two-phase ledger auditing.
    """

    def __init__(
        self,
        ledger_client: Any,
        max_autonomous_level: CommitmentLevel = CommitmentLevel.LEVEL_3_STAGED_EXTERNAL,
        require_adversarial_clearance: bool = True,
    ):
        if not hasattr(ledger_client, "record_decision_view") or not callable(ledger_client.record_decision_view):
            raise LedgerContractError(
                "Provided ledger_client does not implement callable 'record_decision_view(record)'."
            )
        self._ledger = ledger_client
        self._max_autonomous_level = max_autonomous_level
        self._require_adversarial_clearance = require_adversarial_clearance

    def process(self, decision_record: Any) -> ActionGateResult:
        audit_hash = getattr(decision_record, "audit_hash", None)
        if not audit_hash:
            raise ValueError("DecisionRecord missing required 'audit_hash'.")

        if self._require_adversarial_clearance:
            self._assert_adversarial_precondition(decision_record, audit_hash)

        action_name = getattr(decision_record, "action", "unknown_action")
        context = getattr(decision_record, "context", {}) or {}

        commitment_eval: CommitmentEvaluation = classify_commitment_level(action_name, context)

        permitted = True
        rejection_reason = None
        target = DispatchTarget.SEND

        if commitment_eval.level > self._max_autonomous_level:
            target = DispatchTarget.WAIT
            permitted = False
            rejection_reason = (
                f"Commitment level {commitment_eval.level.value} exceeds autonomous "
                f"threshold {self._max_autonomous_level.value}. Shifted to WAIT queue."
            )
        else:
            if commitment_eval.level == CommitmentLevel.LEVEL_0_OBSERVATIONAL:
                target = DispatchTarget.RESEARCH
            elif commitment_eval.level == CommitmentLevel.LEVEL_3_STAGED_EXTERNAL:
                target = DispatchTarget.DRAFT
            else:
                target = DispatchTarget.SEND

        self._record_ledger_audit(
            audit_hash=audit_hash,
            commitment_eval=commitment_eval,
            dispatch_target=target,
            permitted=permitted,
            rejection_reason=rejection_reason,
            status="intent_recorded",
            route=target.value,
        )

        gate_result = ActionGateResult(
            permitted=permitted,
            audit_hash=audit_hash,
            commitment_level=commitment_eval.level,
            dispatch_target=target,
            rejection_reason=rejection_reason,
            telemetry={
                "reversibility_half_life": commitment_eval.reversibility_half_life_seconds,
                "invariants": commitment_eval.invariants_checked,
            }
        )

        if not permitted:
            return gate_result

        try:
            receipt = route_dispatch(target=target, decision_record=decision_record, gate_result=gate_result)
            gate_result.dispatch_receipt = receipt

            self._record_ledger_audit(
                audit_hash=audit_hash,
                commitment_eval=commitment_eval,
                dispatch_target=target,
                permitted=True,
                rejection_reason=None,
                status="actuation_committed",
                route=target.value,
            )
        except DispatchExecutionError as fault:
            self._record_ledger_audit(
                audit_hash=audit_hash,
                commitment_eval=commitment_eval,
                dispatch_target=target,
                permitted=False,
                rejection_reason=f"Actuation Fault: {fault.reason}",
                status="dispatch_failed",
                route=target.value,
            )
            raise

        return gate_result

    def _assert_adversarial_precondition(self, decision_record: Any, audit_hash: str) -> None:
        """
        Structurally validates gate_results contains an active, valid AdversarialClearance
        token, AND cryptographically re-verifies the token's hash against the actual
        record payload it claims to certify. A structurally valid but hash-mismatched
        token is rejected identically to a missing one.
        """
        gate_results = getattr(decision_record, "gate_results", None)
        if not isinstance(gate_results, dict):
            self._record_precondition_fault(audit_hash, "gate_results dictionary missing from DecisionRecord.")
            raise AdversarialPreconditionError(
                f"Record [audit_hash={audit_hash}] rejected: gate_results missing or malformed."
            )

        clearance_data = gate_results.get("adversarial_clearance")
        if clearance_data is None:
            self._record_precondition_fault(audit_hash, "adversarial_clearance token absent in gate_results.")
            raise AdversarialPreconditionError(
                f"Record [audit_hash={audit_hash}] rejected: adversarial_clearance token absent."
            )

        try:
            if isinstance(clearance_data, AdversarialClearance):
                clearance = clearance_data
            else:
                clearance = AdversarialClearance(**clearance_data)
        except Exception as exc:
            self._record_precondition_fault(audit_hash, f"Malformed adversarial clearance payload: {str(exc)}")
            raise AdversarialPreconditionError(
                f"Record [audit_hash={audit_hash}] rejected: malformed clearance contract: {str(exc)}"
            ) from exc

        if not clearance.is_cleared:
            self._record_precondition_fault(audit_hash, f"Adversarial clearance status is {clearance.status.value}")
            raise AdversarialPreconditionError(
                f"Record [audit_hash={audit_hash}] rejected: adversarial status is '{clearance.status.value}' (expected CLEARED)."
            )

        # Cryptographic re-verification: recompute the hash from the actual record
        # payload and confirm it matches what the token claims. Prevents a stale
        # or forged CLEARED token (with a garbage hash) from silently passing.
        action_name = getattr(decision_record, "action", "")
        context = getattr(decision_record, "context", {}) or {}
        expected_hash = AdversarialClearance.generate_verification_hash(
            audit_hash=audit_hash,
            action=action_name,
            context=context,
            status=clearance.status,
            timestamp_iso=clearance.timestamp.isoformat(),
        )
        if expected_hash != clearance.verification_hash:
            self._record_precondition_fault(
                audit_hash,
                "Adversarial clearance hash mismatch: token does not match record payload. "
                "Possible stale or forged clearance."
            )
            raise AdversarialPreconditionError(
                f"Record [audit_hash={audit_hash}] rejected: clearance verification_hash mismatch."
            )

    def _record_precondition_fault(self, audit_hash: str, reason: str) -> None:
        emit_ledger_view(
            ledger_client=self._ledger,
            audit_hash=audit_hash,
            view_name="action_gate_evaluation",
            view_data={
                "status": "precondition_failed",
                "permitted": False,
                "rejection_reason": reason,
            },
            route=None,
        )

    def _record_ledger_audit(
        self,
        audit_hash: str,
        commitment_eval: CommitmentEvaluation,
        dispatch_target: DispatchTarget,
        permitted: bool,
        rejection_reason: Optional[str],
        status: str,
        route: Optional[str] = None,
    ) -> None:
        audit_payload = {
            "status": status,
            "commitment_level": commitment_eval.level.value,
            "commitment_rationale": commitment_eval.operational_rationale,
            "dispatch_target": dispatch_target.value,
            "permitted": permitted,
            "rejection_reason": rejection_reason,
            "invariants_checked": commitment_eval.invariants_checked,
        }
        emit_ledger_view(
            ledger_client=self._ledger,
            audit_hash=audit_hash,
            view_name="action_gate_evaluation",
            view_data=audit_payload,
            route=route,
        )
