"""
Commitment Gradient Evaluator.

Classifies incoming DecisionRecords across an independent 0-5 irreversibility
scale based strictly on operational permanence and error-reversibility,
orthogonal to blast-radius metrics.
"""

from enum import IntEnum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class CommitmentLevel(IntEnum):
    """
    Actuation irreversibility scale (0 to 5).

    0 - Ephemeral / Pure Observation: Read-only query, zero external footprint.
    1 - Internal Mutable State: Reversible in-memory or staging cache updates.
    2 - Local Persistent Mutation: Idempotent or fully roll-backable database writes.
    3 - Staged External State: Drafts, dry-runs, or cancellable external queues.
    4 - Low-Latency External Emission: Asynchronous outbound messages, webhook notifications.
    5 - Irrevocable Material Commitment: Uncancellable financial, legal, or broadcast emissions.
    """
    LEVEL_0_OBSERVATIONAL = 0
    LEVEL_1_INTERNAL_MUTABLE = 1
    LEVEL_2_LOCAL_PERSISTENT = 2
    LEVEL_3_STAGED_EXTERNAL = 3
    LEVEL_4_EXTERNAL_EMISSION = 4
    LEVEL_5_IRREVOCABLE_MATERIAL = 5


class CommitmentEvaluation(BaseModel):
    """Output contract for commitment gradient assessment."""
    level: CommitmentLevel
    reversibility_half_life_seconds: Optional[float] = Field(
        default=None,
        description="Estimated window within which an error-correcting signal can abort or roll back state."
    )
    operational_rationale: str
    invariants_checked: Dict[str, bool] = Field(default_factory=dict)
    is_unmapped_action: bool = Field(
        default=False,
        description="True if action_name had no entry in ACTION_PRIMITIVE_GRADIENT and was defaulted to a high-caution level."
    )


ACTION_PRIMITIVE_GRADIENT: Dict[str, CommitmentLevel] = {
    "query": CommitmentLevel.LEVEL_0_OBSERVATIONAL,
    "inspect": CommitmentLevel.LEVEL_0_OBSERVATIONAL,
    "calculate": CommitmentLevel.LEVEL_0_OBSERVATIONAL,
    "cache_put": CommitmentLevel.LEVEL_1_INTERNAL_MUTABLE,
    "stage_buffer": CommitmentLevel.LEVEL_1_INTERNAL_MUTABLE,
    "db_upsert": CommitmentLevel.LEVEL_2_LOCAL_PERSISTENT,
    "file_write": CommitmentLevel.LEVEL_2_LOCAL_PERSISTENT,
    "draft_message": CommitmentLevel.LEVEL_3_STAGED_EXTERNAL,
    "stage_outbound": CommitmentLevel.LEVEL_3_STAGED_EXTERNAL,
    "publish_event": CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION,
    "send_notification": CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION,
    "send_counteroffer": CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION,
    "send_negotiation_acceptance": CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION,
    "execute_trade": CommitmentLevel.LEVEL_5_IRREVOCABLE_MATERIAL,
    "broadcast_external": CommitmentLevel.LEVEL_5_IRREVOCABLE_MATERIAL,
    "sign_contract": CommitmentLevel.LEVEL_5_IRREVOCABLE_MATERIAL,
}

# State-space invariant: an action_name absent from the gradient map is an
# UNKNOWN state, not a MODERATE one. Defaulting unknown states to a mid-scale
# value silently treats "we have no information" as "we have assessed this as
# moderate risk" -- a false invariant. Unknown state defaults to LEVEL_4
# (high caution, requires explicit clearance) until a real classification is
# added to the map above.
UNMAPPED_ACTION_DEFAULT_LEVEL = CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION


def classify_commitment_level(action_name: str, payload: Dict[str, Any]) -> CommitmentEvaluation:
    """
    Evaluates the operational irreversibility of an action without coupling to blast-radius.
    """
    action_key = action_name.lower().strip()
    is_unmapped = action_key not in ACTION_PRIMITIVE_GRADIENT
    base_level = ACTION_PRIMITIVE_GRADIENT.get(action_key, UNMAPPED_ACTION_DEFAULT_LEVEL)

    is_idempotent = bool(payload.get("idempotent", False))
    has_undo_hook = bool(payload.get("rollback_handler") or payload.get("undo_available"))
    is_external = bool(payload.get("external_counterparty", False))
    broadcast_scope = payload.get("broadcast_scope", "internal")

    invariants = {
        "idempotent": is_idempotent,
        "undo_hook_present": has_undo_hook,
        "crosses_trust_boundary": is_external,
        "broadcast": broadcast_scope == "public",
    }

    effective_level = base_level

    if is_external and broadcast_scope == "public":
        effective_level = max(effective_level, CommitmentLevel.LEVEL_5_IRREVOCABLE_MATERIAL)
    elif is_external and effective_level < CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION:
        effective_level = CommitmentLevel.LEVEL_4_EXTERNAL_EMISSION
    elif has_undo_hook and effective_level in (CommitmentLevel.LEVEL_2_LOCAL_PERSISTENT, CommitmentLevel.LEVEL_3_STAGED_EXTERNAL):
        effective_level = CommitmentLevel(max(0, effective_level - 1))

    # State-space invariant: idempotency is defined by this module's own
    # docstring as part of what qualifies a write as LEVEL_2 ("Idempotent or
    # fully roll-backable database writes"). A LEVEL_2 action explicitly
    # marked non-idempotent has NOT met that invariant and must not be scored
    # as if it had -- promote it one level to reflect the unverified rollback
    # risk. Idempotent LEVEL_2 actions are unaffected (they already satisfy
    # the invariant the level's definition requires).
    if effective_level == CommitmentLevel.LEVEL_2_LOCAL_PERSISTENT and not is_idempotent and not has_undo_hook:
        effective_level = CommitmentLevel.LEVEL_3_STAGED_EXTERNAL

    half_life: Optional[float] = None
    if effective_level == CommitmentLevel.LEVEL_0_OBSERVATIONAL:
        half_life = float("inf")
    elif effective_level <= CommitmentLevel.LEVEL_2_LOCAL_PERSISTENT:
        half_life = 3600.0
    elif effective_level == CommitmentLevel.LEVEL_3_STAGED_EXTERNAL:
        half_life = float(payload.get("retention_window_seconds", 300.0))
    else:
        half_life = 0.0

    rationale = (
        f"Action '{action_name}' mapped to {effective_level.name} (Base: {base_level.name}, "
        f"Unmapped: {is_unmapped}). External boundary: {is_external}, Idempotent: {is_idempotent}, "
        f"Undo hook: {has_undo_hook}."
    )

    return CommitmentEvaluation(
        level=effective_level,
        reversibility_half_life_seconds=half_life,
        operational_rationale=rationale,
        invariants_checked=invariants,
        is_unmapped_action=is_unmapped,
    )
