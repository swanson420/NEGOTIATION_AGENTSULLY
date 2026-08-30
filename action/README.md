# action/ — Commitment gradient + Action Gate + dispatch

`action_gate.py` is the convergence point after a Decision Package has cleared (or looped once through) the Adversarial Check — see `decision/triage_gate/README.md`. This is where commitment-level classification (0–5) and dispatch actually happen; nothing dispatches before passing through this gate.

Levels 0–5 (informational → material/irreversible). Higher level → stronger gating, independent of what blast-radius alone concluded.

Every pass through the gate produces an audit event written to the ledger (see `ledger/README.md`) — not a separate audit database.

Dispatch targets: research, draft, send, wait, bounce.

## Build plan — real input type, and one honest gap
No other module defines a formal protocol `action/` has to satisfy yet — unlike `ledger/` (`LedgerProtocol`) or `working_context/` (`EpistemicRecord`), there's no interface contract already written elsewhere. So this is built against this README's description directly, plus the two real touchpoints below.

**Input:** the "Decision Package" this README refers to is `DecisionRecord` from `decision/contracts/decision_package.py` — the same object `decision/triage_gate/` already produces (`route`, `action`, `context`, `blast_radius`, `gate_results`, `audit_hash`, `timestamp`, `rationale`). `action_gate.py` receives one of these as its primary input once triage has resolved it to `RouteType.ACT_SILENTLY` (or a bounce route has been cleared).

**Ledger writes:** "every pass through the gate produces an audit event written to the ledger" means calling the real `Ledger` (`ledger/ledger.py`) the same way `decision/triage_gate/`'s stage 5 does — via `record_decision_view()`, keyed on `audit_hash`, not an invented ID. Reuse that pattern exactly; don't build a second audit path.

**Honest gap, not blocking, worth knowing:** `adversarial/counterparty_model.py` (the Adversarial Check this module sits downstream of) is still empty — nothing's built there yet. `action_gate.py` should treat "cleared the Adversarial Check" as a precondition already satisfied by the time it receives a `DecisionRecord`, not something it calls itself. Don't wire a direct dependency on `adversarial/` — that module doesn't exist yet, and when it does, it's upstream of this one, not a peer it calls.

1. **`commitment_gradient.py`** — classifies a `DecisionRecord` into a commitment level (0–5, informational → material/irreversible). Independent of what `blast_radius` alone concluded — this is its own gradient, not a re-derivation of blast-radius severity.
2. **`action_gate.py`** — the convergence point. Takes the `DecisionRecord` + commitment level, decides whether dispatch proceeds, writes the audit event to the ledger, and hands off to `dispatch.py`. Nothing dispatches before passing through here.
3. **`dispatch.py`** — routes to one of the five targets (research, draft, send, wait, bounce) based on `action_gate.py`'s decision.
