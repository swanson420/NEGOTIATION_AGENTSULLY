"""
Adversarial Clearance & Verification Contract.

Provides the schema and cryptographic hash construction for gate clearance tokens.
Structural proof that a DecisionRecord has been evaluated by the counterparty model.
"""

from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class AdversarialClearanceStatus(str, Enum):
    """Enumeration of discrete outcomes from adversarial counterparty evaluation."""
    CLEARED = "CLEARED"
    FLAGGED = "FLAGGED"
    RECURSION_LIMIT_EXCEEDED = "RECURSION_LIMIT_EXCEEDED"
    BYPASS_REJECTED = "BYPASS_REJECTED"


class AdversarialClearance(BaseModel):
    """
    Structural clearance certificate required by ActionGate.
    Carries the evaluation verdict, verifier identity, integrity hash, and loop depth.
    """
    status: AdversarialClearanceStatus
    verifier_id: str
    verification_hash: str
    timestamp: datetime
    recursion_depth: int = Field(default=0, ge=0)
    details: Optional[str] = None

    model_config = {
        "frozen": True,
        "extra": "forbid"
    }

    @property
    def is_cleared(self) -> bool:
        """Predicate checking whether this clearance grants permission to proceed."""
        return self.status == AdversarialClearanceStatus.CLEARED

    @staticmethod
    def generate_verification_hash(
        audit_hash: str,
        action: str,
        context: Dict[str, Any],
        status: AdversarialClearanceStatus,
        timestamp_iso: str,
    ) -> str:
        """
        Constructs a deterministic SHA-256 integrity hash across the decision payload.
        Ensures any post-evaluation payload mutation invalidates the token signature.
        """
        canonical_context = json.dumps(context, sort_keys=True, separators=(",", ":"), default=str)
        payload = f"{audit_hash}|{action}|{canonical_context}|{status.value}|{timestamp_iso}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
