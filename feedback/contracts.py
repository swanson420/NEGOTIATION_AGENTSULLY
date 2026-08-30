"""
Telemetry Event Contracts for Feedback Observability.

Defines the append-only schema for all recorded metric events. Every event
strictly requires an `audit_hash` tying the observation to an immutable ledger provenance record.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class FeedbackEventType(str, Enum):
    """The seven canonical observational telemetry events."""
    CLARIFICATION_REQUESTED = "clarification_requested"
    SILENT_RESOLUTION = "silent_resolution"
    HUMAN_OVERRIDE = "human_override"
    META_UNCERTAINTY = "meta_uncertainty"
    DOMAIN_UNCERTAINTY = "domain_uncertainty"
    POST_HOC_CORRECTION = "post_hoc_correction"
    OUTCOME = "outcome"


class BaseFeedbackEvent(BaseModel):
    """
    Immutable base contract for telemetry events.
    Enforces strict provenance binding to the Ledger via `audit_hash`.
    """
    event_type: FeedbackEventType
    audit_hash: str = Field(
        ...,
        min_length=1,
        description="Immutable reference to the originating DecisionRecord / Ledger audit hash."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of observational event capture."
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured, domain-specific observational telemetry."
    )

    model_config = {
        "frozen": True,
        "extra": "forbid",
    }


# Concrete event models for typing and strict schema validation

class ClarificationRequestedEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.CLARIFICATION_REQUESTED
    payload: Dict[str, Any] = Field(..., description="Details on clarification prompt, missing parameters, or user query.")


class SilentResolutionEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.SILENT_RESOLUTION
    payload: Dict[str, Any] = Field(..., description="Execution path details of autonomously resolved action.")


class HumanOverrideEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.HUMAN_OVERRIDE
    payload: Dict[str, Any] = Field(..., description="Target action, overridden decision, operator identity, and rationale.")


class MetaUncertaintyEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.META_UNCERTAINTY
    payload: Dict[str, Any] = Field(..., description="Model confidence breakdown, epistemic boundary violations, or entropy score.")


class DomainUncertaintyEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.DOMAIN_UNCERTAINTY
    payload: Dict[str, Any] = Field(..., description="Unmapped environmental parameters, novel counterparty behavior, or rule gaps.")


class PostHocCorrectionEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.POST_HOC_CORRECTION
    payload: Dict[str, Any] = Field(..., description="Post-execution correction details, error delta, and human feedback notes.")


class OutcomeEvent(BaseFeedbackEvent):
    event_type: FeedbackEventType = FeedbackEventType.OUTCOME
    payload: Dict[str, Any] = Field(..., description="Empirical real-world result, counterpart response, or terminal success/failure.")
