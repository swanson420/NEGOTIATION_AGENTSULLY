"""
End-to-End Scenario Fixture: Internet Bill Negotiation.

Verbatim Input:
  "My internet bill jumped from $65 to $92. Try to get it back down.
   You can negotiate, but don't agree to a contract longer than 12 months."

Wired strictly to the production 6-channel ContextInterrogator engine.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from interpretation.context_interrogator import ContextInterrogator
from interpretation.models import ExtractedContext
from decision.contracts.adversarial_clearance import AdversarialClearance, AdversarialClearanceStatus
from decision.contracts.decision_package import DecisionRecord, RouteType
from action.commitment_gradient import CommitmentLevel
from action.dispatch import DispatchTarget, register_handler, clear_handlers
from action.action_gate import ActionGate, AdversarialPreconditionError
from adversarial.counterparty_model import CounterpartyModelGate
from feedback.metrics import MetricsCollector
from feedback.contracts import FeedbackEventType
from ledger.adapter import emit_ledger_view, LedgerWriteError, LedgerContractError


SCENARIO_INPUT_RAW = (
    "My internet bill jumped from $65 to $92. Try to get it back down. "
    "You can negotiate, but don't agree to a contract longer than 12 months."
)

SCENARIO_AUDIT_HASH = "0xSCENARIO_INTERNET_BILL_2026_08"


def get_scenario_seed_events() -> List[Dict[str, Any]]:
    """
    Returns prior historical ledger events adhering to ledger.append_event(event_id, payload).
    """
    return [
        {
            "event_id": "cust_hist_001",
            "payload": {
                "customer_id": "cust_9921",
                "customer_since": "2023-04-15T00:00:00Z",
                "prior_plan": "Broadband 300",
                "prior_rate": 65.00,
                "current_rate": 92.00,
                "promo_expiration": "2026-07-31T00:00:00Z",
                "payment_history": "flawless",
                "competitor_fiber_available": True
            }
        }
    ]


def format_ledger_seed_text(seed_events: List[Dict[str, Any]]) -> str:
    """Formats seed records into readable ledger text for the ContextInterrogator."""
    lines = []
    for seed in seed_events:
        p = seed["payload"]
        lines.append(
            f"Prior customer history: rate was ${p['prior_rate']:.2f}, "
            f"current rate is ${p['current_rate']:.2f}, "
            f"competitor fiber is {'available' if p['competitor_fiber_available'] else 'unavailable'}."
        )
    return "\n".join(lines)


class ScenarioExecutionTrace(BaseModel):
    """Execution telemetry captured across the end-to-end run."""
    extracted_context: ExtractedContext
    first_pass_route: RouteType
    first_pass_clearance: AdversarialClearance
    second_pass_route: RouteType
    second_pass_clearance: AdversarialClearance
    terminal_dispatch_result: Dict[str, Any]
    ledger_audit_entries: List[Dict[str, Any]]


def run_internet_bill_scenario(ledger_client: Any) -> ScenarioExecutionTrace:
    """
    Executes the internet-bill scenario through the complete retrofitted pipeline.
    """
    dispatched_actuations = []
    register_handler(
        DispatchTarget.SEND,
        lambda rec, gate: dispatched_actuations.append({"action": rec.action, "audit_hash": rec.audit_hash})
    )

    seed_events = get_scenario_seed_events()
    for seed in seed_events:
        if hasattr(ledger_client, "append_event") and callable(ledger_client.append_event):
            ledger_client.append_event(seed["event_id"], seed["payload"])
        else:
            emit_ledger_view(
                ledger_client=ledger_client,
                audit_hash=SCENARIO_AUDIT_HASH,
                view_name="historical_seed",
                view_data=seed["payload"],
                route=None
            )

    ledger_text = format_ledger_seed_text(seed_events)

    interrogator = ContextInterrogator()
    extracted_context = interrogator.extract_context(
        raw_input=SCENARIO_INPUT_RAW,
        ledger=ledger_text
    )

    assert any("don't agree to a contract longer than 12 months" in c.lower() for c in extracted_context.constraints), (
        f"Contraction negative constraint failed to route to constraints: {extracted_context.constraints}"
    )
    assert any("try to get it back down" in o.lower() for o in extracted_context.objectives), (
        f"Goal imperative failed to route to objectives: {extracted_context.objectives}"
    )

    initial_record = DecisionRecord(
        audit_hash=SCENARIO_AUDIT_HASH,
        action="send_negotiation_acceptance",
        context={
            "offered_rate": 65.00,
            "contract_months": 24,
            "reveals_walkaway_price": True
        },
        recursion_depth=0
    )

    cp_gate = CounterpartyModelGate(ledger_client=ledger_client)
    route_1, clearance_1, bounced_record = cp_gate.evaluate(initial_record)

    assert route_1 == RouteType.BOUNCE
    assert clearance_1.status == AdversarialClearanceStatus.FLAGGED
    assert bounced_record.recursion_depth == 1

    reformulated_record = DecisionRecord(
        audit_hash=SCENARIO_AUDIT_HASH,
        action="send_counteroffer",
        context={
            "offered_rate": 70.00,
            "contract_months": 12,
            "reveals_walkaway_price": False,
            "idempotent": True,
            "external_counterparty": True,
            "broadcast_scope": "private"
        },
        recursion_depth=bounced_record.recursion_depth
    )

    route_2, clearance_2, cleared_record = cp_gate.evaluate(reformulated_record)

    assert route_2 == RouteType.ACT_SILENTLY
    assert clearance_2.status == AdversarialClearanceStatus.CLEARED
    assert cleared_record.gate_results["adversarial_clearance"]["status"] == "CLEARED"

    action_gate = ActionGate(
        ledger_client=ledger_client,
        max_autonomous_level=CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION,
        require_adversarial_clearance=True
    )
    gate_result = action_gate.process(cleared_record)

    assert gate_result.permitted is True
    assert gate_result.dispatch_target == DispatchTarget.SEND

    collector = MetricsCollector(ledger_client=ledger_client)
    collector.record_outcome(
        audit_hash=SCENARIO_AUDIT_HASH,
        payload={
            "scenario": "internet_bill",
            "initial_rate": 92.00,
            "settled_rate": 70.00,
            "contract_months": 12,
            "loopbacks_required": 1,
            "success": True
        }
    )

    raw_entries = getattr(ledger_client, "entries", [])

    return ScenarioExecutionTrace(
        extracted_context=extracted_context,
        first_pass_route=route_1,
        first_pass_clearance=clearance_1,
        second_pass_route=route_2,
        second_pass_clearance=clearance_2,
        terminal_dispatch_result={"permitted": gate_result.permitted, "actuations": dispatched_actuations},
        ledger_audit_entries=raw_entries
    )
