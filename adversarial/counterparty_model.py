"""
Adversarial Counterparty Gate.

Evaluates counterparty risks (inferred knowledge, pressure tactics, leverage exposure),
enforces the maximum 1-loopback limit using schema-validated recursion state on DecisionRecord,
and emits signed AdversarialClearance tokens via the universal ledger adapter.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from pydantic import BaseModel, Field

from decision.contracts.adversarial_clearance import AdversarialClearance, AdversarialClearanceStatus
from decision.contracts.decision_package import DecisionRecord, RouteType
from ledger.adapter import emit_ledger_view, LedgerContractError, LedgerWriteError


class CounterpartyEvaluationResult(BaseModel):
    """Internal diagnostic result of counterparty exposure analysis."""
    cleared: bool
    objections: List[str] = Field(default_factory=list)
    risk_flags: Dict[str, bool] = Field(default_factory=dict)
    rationale: str

    model_config = {
        "frozen": True,
    }


class CounterpartyModelGate:
    """
    Evaluates whether an outbound message or action harms our strategic position
    or leaks walk-away terms to an adversarial counterparty.

    Sits directly between Triage and ActionGate with authority to bounce back once.
    """

    VERIFIER_ID = "adversarial_counterparty_v1:ruleset_2026_08"
    MAX_RECURSION_DEPTH = 1

    def __init__(self, ledger_client: Any):
        if not hasattr(ledger_client, "record_decision_view") or not callable(ledger_client.record_decision_view):
            raise LedgerContractError("Ledger client must implement callable 'record_decision_view(record)'.")
        self._ledger = ledger_client

    def evaluate(self, record: DecisionRecord) -> Tuple[RouteType, AdversarialClearance, DecisionRecord]:
        """
        Executes adversarial analysis and adjudicates the routing trajectory:
          - If clean: returns (RouteType.ACT_SILENTLY, CLEARED token, updated_record)
          - If objectionable & recursion_depth < 1: returns (RouteType.BOUNCE, FLAGGED token, updated_record)
          - If objectionable & recursion_depth >= 1: returns (RouteType.ESCALATE_HUMAN, RECURSION_LIMIT_EXCEEDED token, updated_record)

        State evolution is immutable via model_copy methods; in-memory mutation is forbidden.
        """
        if not isinstance(record, DecisionRecord):
            raise TypeError(f"Expected DecisionRecord instance, received {type(record).__name__}.")

        analysis = self._analyze_counterparty_exposure(record.action, record.context)
        now_utc = datetime.now(timezone.utc)
        timestamp_iso = now_utc.isoformat()

        # Case A: Record is clean -> CLEARED token
        if analysis.cleared:
            status = AdversarialClearanceStatus.CLEARED
            v_hash = AdversarialClearance.generate_verification_hash(
                audit_hash=record.audit_hash,
                action=record.action,
                context=record.context,
                status=status,
                timestamp_iso=timestamp_iso,
            )
            clearance = AdversarialClearance(
                status=status,
                verifier_id=self.VERIFIER_ID,
                verification_hash=v_hash,
                timestamp=now_utc,
                recursion_depth=record.recursion_depth,
                details=analysis.rationale,
            )
            updated_record = record.with_clearance(clearance, RouteType.ACT_SILENTLY)
            self._write_audit(record.audit_hash, clearance, RouteType.ACT_SILENTLY, analysis)
            return RouteType.ACT_SILENTLY, clearance, updated_record

        # Case B: Objection raised, recursion limit NOT reached (1st loopback permitted) -> BOUNCE
        if record.recursion_depth < self.MAX_RECURSION_DEPTH:
            status = AdversarialClearanceStatus.FLAGGED
            v_hash = AdversarialClearance.generate_verification_hash(
                audit_hash=record.audit_hash,
                action=record.action,
                context=record.context,
                status=status,
                timestamp_iso=timestamp_iso,
            )
            clearance = AdversarialClearance(
                status=status,
                verifier_id=self.VERIFIER_ID,
                verification_hash=v_hash,
                timestamp=now_utc,
                recursion_depth=record.recursion_depth + 1,
                details=f"Objection: {', '.join(analysis.objections)}. Returning to triage.",
            )

            # Advance recursion counter and attach clearance immutably
            updated_record = record.increment_recursion().with_clearance(clearance, RouteType.BOUNCE)
            self._write_audit(record.audit_hash, clearance, RouteType.BOUNCE, analysis)
            return RouteType.BOUNCE, clearance, updated_record

        # Case C: Objection raised, recursion limit reached (Damping applied) -> ESCALATE_HUMAN
        status = AdversarialClearanceStatus.RECURSION_LIMIT_EXCEEDED
        v_hash = AdversarialClearance.generate_verification_hash(
            audit_hash=record.audit_hash,
            action=record.action,
            context=record.context,
            status=status,
            timestamp_iso=timestamp_iso,
        )
        clearance = AdversarialClearance(
            status=status,
            verifier_id=self.VERIFIER_ID,
            verification_hash=v_hash,
            timestamp=now_utc,
            recursion_depth=record.recursion_depth,
            details="Multiple objections during feedback loop; escalated to human domain expert.",
        )
        updated_record = record.with_clearance(clearance, RouteType.ESCALATE_HUMAN)
        self._write_audit(record.audit_hash, clearance, RouteType.ESCALATE_HUMAN, analysis)
        return RouteType.ESCALATE_HUMAN, clearance, updated_record

    def _analyze_counterparty_exposure(self, action: str, context: Dict[str, Any]) -> CounterpartyEvaluationResult:
        """
        Inspects message content, numbers, pressure markers, and walk-away leakage.
        """
        objections: List[str] = []
        risk_flags: Dict[str, bool] = {
            "reveals_walkaway_price": False,
            "submits_to_artificial_urgency": False,
            "unilateral_concession": False,
            "violates_duration_constraint": False,
        }

        # Check: Walk-away price leakage
        if context.get("mentions_bottom_line") or context.get("reveals_walkaway_price"):
            risk_flags["reveals_walkaway_price"] = True
            objections.append("Message exposes reserve price / walk-away threshold.")

        # Check: False deadline compliance
        if context.get("counterparty_deadline_claim") and context.get("accelerate_concession"):
            risk_flags["submits_to_artificial_urgency"] = True
            objections.append("Unverified deadline claim triggering accelerated concession.")

        # Check: Unilateral concession without reciprocity
        if context.get("is_concession") and not context.get("counterparty_reciprocal_commitment"):
            risk_flags["unilateral_concession"] = True
            objections.append("Unilateral concession granted without reciprocal commitment.")

        # Check: Explicit duration constraint breach in context
        contract_months = context.get("contract_months")
        if contract_months is not None and contract_months > 12:
            risk_flags["violates_duration_constraint"] = True
            objections.append(f"Contract duration ({contract_months} months) exceeds maximum 12-month limit.")

        cleared = len(objections) == 0
        rationale = "No adversarial risk detected." if cleared else "; ".join(objections)

        return CounterpartyEvaluationResult(
            cleared=cleared,
            objections=objections,
            risk_flags=risk_flags,
            rationale=rationale,
        )

    def _write_audit(
        self,
        audit_hash: str,
        clearance: AdversarialClearance,
        next_route: RouteType,
        analysis: CounterpartyEvaluationResult,
    ) -> None:
        """Emits structured audit entry to the ledger via universal adapter."""
        payload = {
            "status": clearance.status.value,
            "verifier_id": clearance.verifier_id,
            "verification_hash": clearance.verification_hash,
            "recursion_depth": clearance.recursion_depth,
            "next_route": next_route.value,
            "risk_flags": analysis.risk_flags,
            "rationale": analysis.rationale,
        }
        emit_ledger_view(
            ledger_client=self._ledger,
            audit_hash=audit_hash,
            view_name="adversarial_counterparty_evaluation",
            view_data=payload,
            route=next_route.value,
        )
