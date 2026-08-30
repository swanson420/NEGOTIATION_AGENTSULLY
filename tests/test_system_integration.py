"""
Unified End-to-End System Integration & Contract Verification Suite.

Validates the full chain of control:
  1. Universal ledger adapter write protocol and boolean return checking.
  2. 6-channel ContextInterrogator sensory extraction with contraction support.
  3. Action gate commitment gradient and two-phase audit lifecycle.
  4. Reconciliation loop fault isolation and idempotent replay.
  5. Multi-branch counterparty gate evaluation (BOUNCE, CLEARED, ESCALATE_HUMAN).
  6. Complete internet-bill scenario closed-loop execution.
"""

from datetime import datetime, timezone
import pytest
from typing import Any, Dict, List, Optional

from decision.contracts.adversarial_clearance import AdversarialClearance, AdversarialClearanceStatus
from decision.contracts.decision_package import DecisionRecord, RouteType
from action.commitment_gradient import CommitmentLevel, classify_commitment_level
from action.dispatch import DispatchTarget, register_handler, clear_handlers, DispatchExecutionError
from action.action_gate import ActionGate, AdversarialPreconditionError
from action.reconciliation import ActuationReconciler
from adversarial.counterparty_model import CounterpartyModelGate
from feedback.metrics import MetricsCollector
from feedback.reader import TelemetryReader
from feedback.contracts import FeedbackEventType
from interpretation.context_interrogator import ContextInterrogator
from ledger.adapter import emit_ledger_view, LedgerWriteError, LedgerContractError
from scenarios.internet_bill.scenario import (
    SCENARIO_INPUT_RAW,
    SCENARIO_AUDIT_HASH,
    run_internet_bill_scenario,
)


class ConformingRealLedger:
    """
    Exact simulation of ledger/ledger.py:
      - Takes exactly 1 positional argument (`record`).
      - Returns bool (True on success, False on failure).
      - Never raises on validation rejections; caller must fail closed.
    """
    def __init__(self, reject_all: bool = False, reject_after: int = -1):
        self.entries: List[Dict[str, Any]] = []
        self.uncommitted_views: List[Dict[str, Any]] = []
        self.reject_all = reject_all
        self.reject_after = reject_after
        self.call_count = 0

    def record_decision_view(self, record: Any) -> bool:
        self.call_count += 1
        if self.reject_all:
            return False
        if self.reject_after != -1 and self.call_count > self.reject_after:
            return False
        if not isinstance(record, dict):
            return False
        if "audit_hash" not in record or "view_name" not in record:
            return False
        self.entries.append(dict(record))
        return True

    def get_uncommitted_views(self, view_name: str, older_than_seconds: float) -> List[Dict[str, Any]]:
        return self.uncommitted_views

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        for entry in self.entries:
            if entry.get("audit_hash") == event_id:
                return entry
        return None


@pytest.fixture(autouse=True)
def clean_handlers():
    clear_handlers()
    yield
    clear_handlers()


# ---------------------------------------------------------------------------
# 1. Adapter Contract & Boolean Fail-Closed Tests
# ---------------------------------------------------------------------------

def test_adapter_emits_valid_record_structure():
    ledger = ConformingRealLedger()
    emit_ledger_view(
        ledger_client=ledger,
        audit_hash="0xTEST_ADAPT_1",
        view_name="action_gate_evaluation",
        view_data={"status": "intent_recorded"},
        route=RouteType.ACT_SILENTLY.value
    )

    assert len(ledger.entries) == 1
    rec = ledger.entries[0]
    assert rec["audit_hash"] == "0xTEST_ADAPT_1"
    assert rec["view_name"] == "action_gate_evaluation"
    assert rec["route"] == "act_silently"
    assert rec["status"] == "intent_recorded"


def test_adapter_fails_closed_when_ledger_returns_false():
    ledger = ConformingRealLedger(reject_all=True)
    with pytest.raises(LedgerWriteError) as exc_info:
        emit_ledger_view(
            ledger_client=ledger,
            audit_hash="0xTEST_FAIL",
            view_name="test_view",
            view_data={}
        )
    assert "Ledger rejected audit write" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Context Interrogator 6-Channel & Contraction Tests
# ---------------------------------------------------------------------------

def test_context_interrogator_contraction_extraction():
    interrogator = ContextInterrogator()
    raw = "My internet bill jumped from $65 to $92. Try to get it back down. You can negotiate, but don't agree to a contract longer than 12 months."
    ledger_text = "Prior customer history: rate was $65.00."

    ctx = interrogator.extract_context(raw, ledger_text)

    assert len(ctx.constraints) >= 1
    assert any("don't agree to a contract longer than 12 months" in c.lower() for c in ctx.constraints)
    assert any("try to get it back down" in o.lower() for o in ctx.objectives)
    assert any("jumped from $65 to $92" in f.lower() for f in ctx.facts)


# ---------------------------------------------------------------------------
# 3. Action Gate Lifecycle & Precondition Tests
# ---------------------------------------------------------------------------

def test_action_gate_precondition_rejection():
    ledger = ConformingRealLedger()
    gate = ActionGate(ledger_client=ledger, require_adversarial_clearance=True)

    record = DecisionRecord(audit_hash="0xNO_CLEARANCE", action="query", context={})

    with pytest.raises(AdversarialPreconditionError):
        gate.process(record)

    assert len(ledger.entries) == 1
    assert ledger.entries[0]["status"] == "precondition_failed"
    assert "route" not in ledger.entries[0]


def test_action_gate_two_phase_audit_success():
    register_handler(DispatchTarget.SEND, lambda rec, gate: {"status": "dispatched"})
    ledger = ConformingRealLedger()
    gate = ActionGate(ledger_client=ledger, require_adversarial_clearance=False)

    record = DecisionRecord(audit_hash="0xGATE_PASS", action="send_notification", context={"idempotent": True})
    result = gate.process(record)

    assert result.permitted is True
    assert len(ledger.entries) == 2
    assert ledger.entries[0]["status"] == "intent_recorded"
    assert ledger.entries[1]["status"] == "actuation_committed"


# ---------------------------------------------------------------------------
# 4. Reconciliation Fault Isolation Tests
# ---------------------------------------------------------------------------

def test_reconciliation_isolates_faulty_item_and_continues_sweep():
    ledger = ConformingRealLedger()

    item1 = DecisionRecord(audit_hash="0xRECON_1", action="broadcast_external", context={"idempotent": False})
    item2 = DecisionRecord(audit_hash="0xRECON_2", action="db_upsert", context={"idempotent": True})

    ledger.uncommitted_views = [
        {"audit_hash": "0xRECON_1", "decision_record": item1, "dispatch_target": "send", "timestamp": datetime.now(timezone.utc), "elapsed_seconds": 40.0},
        {"audit_hash": "0xRECON_2", "decision_record": item2, "dispatch_target": "send", "timestamp": datetime.now(timezone.utc), "elapsed_seconds": 40.0},
    ]

    executed = []
    register_handler(DispatchTarget.SEND, lambda rec, gate: executed.append(rec.audit_hash))

    reconciler = ActuationReconciler(ledger_client=ledger)
    report = reconciler.scan_and_reconcile()

    assert report.scanned_count == 2
    assert report.reconciled_count == 2
    assert len(executed) == 1
    assert executed[0] == "0xRECON_2"
    assert ledger.entries[0]["status"] == "reconciliation_aborted_non_idempotent"
    assert ledger.entries[1]["status"] == "actuation_reconciled"


# ---------------------------------------------------------------------------
# 5. Counterparty Gate Multi-Branch Tests
# ---------------------------------------------------------------------------

def test_counterparty_gate_all_three_branches():
    ledger = ConformingRealLedger()
    gate = CounterpartyModelGate(ledger_client=ledger)

    rec_bounce = DecisionRecord(audit_hash="0xCP_1", action="send_notification", context={"reveals_walkaway_price": True}, recursion_depth=0)
    r1, c1, upd1 = gate.evaluate(rec_bounce)
    assert r1 == RouteType.BOUNCE
    assert c1.status == AdversarialClearanceStatus.FLAGGED
    assert upd1.recursion_depth == 1

    rec_clean = DecisionRecord(audit_hash="0xCP_2", action="send_notification", context={"reveals_walkaway_price": False}, recursion_depth=0)
    r2, c2, upd2 = gate.evaluate(rec_clean)
    assert r2 == RouteType.ACT_SILENTLY
    assert c2.status == AdversarialClearanceStatus.CLEARED

    rec_escalate = DecisionRecord(audit_hash="0xCP_3", action="send_notification", context={"reveals_walkaway_price": True}, recursion_depth=1)
    r3, c3, upd3 = gate.evaluate(rec_escalate)
    assert r3 == RouteType.ESCALATE_HUMAN
    assert c3.status == AdversarialClearanceStatus.RECURSION_LIMIT_EXCEEDED


# ---------------------------------------------------------------------------
# 6. End-to-End Internet Bill Scenario Execution
# ---------------------------------------------------------------------------

def test_internet_bill_scenario_complete_closed_loop():
    ledger = ConformingRealLedger()
    trace = run_internet_bill_scenario(ledger_client=ledger)

    assert len(trace.extracted_context.constraints) >= 1
    assert any("don't agree to a contract longer than 12 months" in c.lower() for c in trace.extracted_context.constraints)

    assert trace.first_pass_route == RouteType.BOUNCE
    assert trace.second_pass_route == RouteType.ACT_SILENTLY
    assert trace.terminal_dispatch_result["permitted"] is True
    assert len(trace.terminal_dispatch_result["actuations"]) == 1

    view_names = [e["view_name"] for e in ledger.entries]
    assert view_names == [
        "historical_seed",
        "adversarial_counterparty_evaluation",
        "adversarial_counterparty_evaluation",
        "action_gate_evaluation",
        "action_gate_evaluation",
        "metric_outcome"
    ]
