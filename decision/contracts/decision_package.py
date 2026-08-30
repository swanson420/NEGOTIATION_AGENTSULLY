"""
Decision Package Contract.

Defines the core DecisionRecord container passed across Triage, Adversarial Check,
and Action Gate. Enforces immutable state transformations and an explicit
recursion counter to guarantee closed-loop damping across serialization boundaries.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

from decision.contracts.adversarial_clearance import AdversarialClearance


class RouteType(str, Enum):
    """
    Exhaustive routing trajectories for decision dispatch and feedback.
    """
    ACT_SILENTLY = "act_silently"
    BOUNCE = "bounce"
    RESEARCH = "research"
    DRAFT = "draft"
    WAIT = "wait"
    ESCALATE_HUMAN = "escalate_human"


class DecisionRecord(BaseModel):
    """
    Immutable state record for an evaluated operational decision.

    Every mutation yields a new instance via model_copy; dynamic in-place
    attribute assignment is forbidden to prevent state amnesia across
    process or serialization boundaries.
    """
    audit_hash: str = Field(
        ...,
        min_length=1,
        description="Immutable provenance root tying this decision to the Ledger."
    )
    action: str = Field(
        ...,
        min_length=1,
        description="Operational primitive or action verb being evaluated."
    )
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="Operational context, domain parameters, and invariant markers."
    )
    blast_radius: str = Field(
        default="LOCAL",
        description="Spatial/systemic scope of potential impact (independent of commitment gradient)."
    )
    route: Optional[RouteType] = Field(
        default=None,
        description="Adjudicated actuation trajectory."
    )
    recursion_depth: int = Field(
        default=0,
        ge=0,
        description="Explicit loopback iteration counter preventing undamped feedback oscillation."
    )
    gate_results: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured verification tokens emitted by triage and adversarial gates."
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp of decision record instantiation."
    )
    rationale: Optional[str] = Field(
        default=None,
        description="Human-readable justification for the proposed route or action."
    )

    model_config = {
        "frozen": True,
        "extra": "forbid",
    }

    def increment_recursion(self) -> "DecisionRecord":
        """
        Advances the recursion depth by 1, returning a new immutable instance.
        Used by loopback gates to enforce loop limits deterministically.
        """
        return self.model_copy(update={"recursion_depth": self.recursion_depth + 1})

    def with_clearance(self, clearance: AdversarialClearance, new_route: RouteType) -> "DecisionRecord":
        """
        Attaches a verified AdversarialClearance certificate and assigns the target route,
        returning a new immutable instance.
        """
        updated_gate_results = dict(self.gate_results)
        updated_gate_results["adversarial_clearance"] = clearance
        return self.model_copy(update={
            "gate_results": updated_gate_results,
            "route": new_route,
        })

    def with_route(self, new_route: RouteType, rationale: Optional[str] = None) -> "DecisionRecord":
        """
        Updates the routing trajectory and optional rationale on a new immutable instance.
        """
        updates: Dict[str, Any] = {"route": new_route}
        if rationale is not None:
            updates["rationale"] = rationale
        return self.model_copy(update=updates)
