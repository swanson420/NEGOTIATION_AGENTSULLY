# adversarial/ — Counterparty Model — a GATE on the decision, not a sibling of action/

Corrected in Claude #32 / build-plan.md: this module does not run in parallel with `action/` and feed into a shared downstream step. It sits between Triage's Decision Package and the Action Gate, with the authority to bounce the package back into Triage once.

Why this matters concretely: without this correction, an action could be dispatched and adversarial analysis could run concurrently, discovering a problem (e.g. "this wording reveals our walk-away price") only after the send — after reversibility was already spent. The gate placement prevents that.

Models: what the counterparty wants, knows, is likely to infer, their incentives, how our message could be used against us, which response worsens vs. protects our position.

Triggered contextually (pressure tactics, inconsistent numbers, deadline claims in incoming messages) — logged as its own toggle event.

One return loop into Triage maximum. A second objection on the same package escalates to a human bounce (domain uncertainty), it does not loop again.

## Build plan — real input, and a real dependency this module can't satisfy alone
No `evaluator.py` or `loopback_coordinator.py` exist in this tree — despite the name, this is a single-file build: `counterparty_model.py` only.

**Input:** the "Decision Package" is `DecisionRecord` (`decision/contracts/decision_package.py`) — same object `decision/triage_gate/` produces. This module evaluates a `DecisionRecord` before it reaches `action/`'s gate, with authority to bounce it back into Triage.

**Real, unresolved dependency — flag this, don't silently work around it:** "one return loop into Triage maximum" is recursion-cap enforcement, and that logic doesn't exist anywhere in the current codebase. It was part of an older generation (`decision/triage_gate/recursion_guard.py` still has real logic in it — `RecursionCapExceeded`, `authorize_next_pass`) but nothing in the current `stages/`-based pipeline calls it, and no test exercises it. This module's core one-loop-max behavior depends on that cap existing somewhere both `decision/triage_gate/` and this module can share — it can't just be reimplemented locally inside `counterparty_model.py`, or two different modules end up enforcing two different, possibly-disagreeing loop counters. This needs a real decision about where the shared cap lives before `counterparty_model.py` can be built correctly — not a vague ask to Gogglemeister, the same caveat as before.
