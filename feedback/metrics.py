"""
Passive Telemetry Collector (Metrics Only, No Auto-Tuning).

Captures observational events linked to decision audit hashes. Does NOT perform
policy evaluation, threshold shifting, running aggregation, or control feedback.
Commits directly to the Ledger via the universal `emit_ledger_view` adapter.
"""

from typing import Any, Dict, Optional

from feedback.contracts import (
    BaseFeedbackEvent,
    FeedbackEventType,
    ClarificationRequestedEvent,
    SilentResolutionEvent,
    HumanOverrideEvent,
    MetaUncertaintyEvent,
    DomainUncertaintyEvent,
    PostHocCorrectionEvent,
    OutcomeEvent,
)
from ledger.adapter import emit_ledger_view, LedgerContractError, LedgerWriteError


class MetricsCollector:
    """
    Append-only observational telemetry sink.

    Adheres strictly to passive observability:
      1. Every event requires an explicit `audit_hash`.
      2. Events are committed directly to the Ledger via `emit_ledger_view` (route=None).
      3. Zero automated tuning, running tallies as source of truth, or dynamic policy changes.
    """

    def __init__(self, ledger_client: Any):
        if not hasattr(ledger_client, "record_decision_view") or not callable(ledger_client.record_decision_view):
            raise LedgerContractError(
                "Provided ledger_client does not implement callable 'record_decision_view(record)'."
            )
        self._ledger = ledger_client

    def record_event(self, event: BaseFeedbackEvent, route: Optional[str] = None) -> BaseFeedbackEvent:
        """
        Base append method. Enforces ledger persistence of the event record via adapter.
        """
        if not isinstance(event, BaseFeedbackEvent):
            raise TypeError(f"Expected BaseFeedbackEvent instance, got {type(event).__name__}.")

        view_data = {
            "event_type": event.event_type.value,
            "payload": event.payload,
        }

        # Telemetry events are non-routing provenance records; route defaults to None
        emit_ledger_view(
            ledger_client=self._ledger,
            audit_hash=event.audit_hash,
            view_name=f"metric_{event.event_type.value}",
            view_data=view_data,
            route=route,
        )

        return event

    # Explicit, strongly-typed recording interfaces for all seven canonical events

    def record_clarification_requested(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> ClarificationRequestedEvent:
        event = ClarificationRequestedEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event

    def record_silent_resolution(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> SilentResolutionEvent:
        event = SilentResolutionEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event

    def record_human_override(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> HumanOverrideEvent:
        event = HumanOverrideEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event

    def record_meta_uncertainty(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> MetaUncertaintyEvent:
        event = MetaUncertaintyEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event

    def record_domain_uncertainty(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> DomainUncertaintyEvent:
        event = DomainUncertaintyEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event

    def record_post_hoc_correction(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> PostHocCorrectionEvent:
        event = PostHocCorrectionEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event

    def record_outcome(
        self, audit_hash: str, payload: Dict[str, Any], route: Optional[str] = None
    ) -> OutcomeEvent:
        event = OutcomeEvent(audit_hash=audit_hash, payload=payload)
        self.record_event(event, route=route)
        return event
