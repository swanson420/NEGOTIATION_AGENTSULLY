"""
Real-Postgres Ledger Integration Suite.

Mirrors tests/test_system_integration.py's ledger-facing scenarios against
PostgresLedger instead of the in-memory ConformingRealLedger mock, verifying
rows are actually durable and queryable back out through an independent
connection -- not just visible via the ledger object's own state.

Skipped entirely when DATABASE_URL is unset (local dev without Postgres,
or any environment that hasn't provisioned one). On Railway, DATABASE_URL
is set, so this suite runs for real in the deploy log.

test_context_interrogator_contraction_extraction is intentionally not
ported here: it exercises zero ledger interaction, so it adds nothing by
running twice.
"""

import os
import uuid
from datetime import datetime, timezone

import psycopg2
import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="DATABASE_URL not set; skipping real-Postgres ledger integration tests.",
)

from decision.contracts.decision_package import DecisionRecord, RouteType
from action.commitment_gradient import CommitmentLevel
from action.dispatch import DispatchTarget, register_handler, clear_handlers
from action.action_gate import ActionGate, AdversarialPreconditionError
from action.reconciliation import ActuationReconciler
from adversarial.counterparty_model import CounterpartyModelGate
from ledger.adapter import emit_ledger_view, LedgerWriteError
from ledger.postgres_ledger import PostgresLedger
from scenarios.internet_bill.scenario import run_internet_bill_scenario


@pytest.fixture(autouse=True)
def clean_handlers():
    clear_handlers()
    yield
    clear_handlers()


@pytest.fixture
def pg_ledger():
    table_name = f"ledger_test_{uuid.uuid4().hex[:12]}"
    ledger = PostgresLedger(dsn=DATABASE_URL, table_name=table_name)
    yield ledger
    with psycopg2.connect(DATABASE_URL) as cleanup_conn:
        with cleanup_conn.cursor() as cur:
            cur.execute(f'DROP TABLE IF EXISTS "{table_name}"')
        cleanup_conn.commit()
    ledger.close()


def _fetch_raw_rows(table_name):
    """Independent connection, deliberately separate from the ledger under test,
    to prove persistence rather than trusting the ledger's own view of itself."""
    with psycopg2.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT audit_hash, view_name, route, payload FROM "{table_name}" ORDER BY id ASC'
            )
            rows = cur.fetchall()
    return [
        {"audit_hash": r[0], "view_name": r[1], "route": r[2], **(r[3] or {})}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# 1. Adapter Contract & Boolean Fail-Closed Tests
# ---------------------------------------------------------------------------

def test_adapter_emits_valid_record_structure_real_postgres(pg_ledger):
    emit_ledger_view(
        ledger_client=pg_ledger,
        audit_hash="0xTEST_ADAPT_1",
        view_name="action_gate_evaluation",
        view_data={"status": "intent_recorded"},
        route=RouteType.ACT_SILENTLY.value,
    )

    rows = _fetch_raw_rows(pg_ledger._table)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["audit_hash"] == "0xTEST_ADAPT_1"
    assert rec["view_name"] == "action_gate_evaluation"
    assert rec["route"] == "act_silently"
    assert rec["status"] == "intent_recorded"


def test_adapter_fails_closed_on_real_connection_failure(pg_ledger):
    """
    Instead of the mock's artificial reject_all flag, force a genuine
    Postgres failure: kill the connection and point the DSN at an
    unreachable database, so the reconnect-on-write path fails for real.
    """
    pg_ledger._conn.close()
    pg_ledger._dsn = "postgresql://invalid_user:wrong_password@localhost:5432/nonexistent_db_xyz"

    with pytest.raises(LedgerWriteError) as exc_info:
        emit_ledger_view(
            ledger_client=pg_ledger,
            audit_hash="0xTEST_FAIL",
            view_name="test_view",
            view_data={},
        )
    assert "Ledger rejected audit write" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Action Gate Lifecycle & Precondition Tests
# ---------------------------------------------------------------------------

def test_action_gate_precondition_rejection_real_postgres(pg_ledger):
    gate = ActionGate(ledger_client=pg_ledger, require_adversarial_clearance=True)
    record = DecisionRecord(audit_hash="0xNO_CLEARANCE", action="query", context={})

    with pytest.raises(AdversarialPreconditionError):
        gate.process(record)

    rows = _fetch_raw_rows(pg_ledger._table)
    assert len(rows) == 1
    assert rows[0]["status"] == "precondition_failed"
    assert "route" not in rows[0] or rows[0]["route"] is None


def test_action_gate_two_phase_audit_success_real_postgres(pg_ledger):
    register_handler(DispatchTarget.SEND, lambda rec, gate: {"status": "dispatched"})
    gate = ActionGate(ledger_client=pg_ledger, require_adversarial_clearance=False)

    record = DecisionRecord(audit_hash="0xGATE_PASS", action="send_notification", context={"idempotent": True})
    result = gate.process(record)

    assert result.permitted is True

    rows = _fetch_raw_rows(pg_ledger._table)
    assert len(rows) == 2
    assert rows[0]["status"] == "intent_recorded"
    assert rows[1]["status"] == "actuation_committed"


# ---------------------------------------------------------------------------
# 3. Reconciliation Fault Isolation Against Real Persisted Rows
# ---------------------------------------------------------------------------

def test_reconciliation_isolates_faulty_item_and_continues_sweep_real_postgres(pg_ledger):
    """
    ActionGate's current audit payload doesn't persist action/context, so a
    real ledger has no way to replay an actuation from what production
    actually writes today -- get_uncommitted_views reconstructs a
    DecisionRecord only when the row's payload happens to carry them. Seed
    those two pending rows directly, exactly as the original mock test set
    ledger.uncommitted_views by hand rather than deriving it.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    assert pg_ledger.record_decision_view({
        "audit_hash": "0xRECON_1",
        "view_name": "action_gate_evaluation",
        "route": "send",
        "timestamp": now_iso,
        "status": "intent_recorded",
        "dispatch_target": "send",
        "action": "broadcast_external",
        "context": {"idempotent": False},
    })
    assert pg_ledger.record_decision_view({
        "audit_hash": "0xRECON_2",
        "view_name": "action_gate_evaluation",
        "route": "send",
        "timestamp": now_iso,
        "status": "intent_recorded",
        "dispatch_target": "send",
        "action": "db_upsert",
        "context": {"idempotent": True},
    })

    executed = []
    register_handler(DispatchTarget.SEND, lambda rec, gate: executed.append(rec.audit_hash))

    reconciler = ActuationReconciler(ledger_client=pg_ledger, timeout_threshold_seconds=0.0)
    report = reconciler.scan_and_reconcile()

    assert report.scanned_count == 2
    assert report.reconciled_count == 2
    assert executed == ["0xRECON_2"]

    rows = _fetch_raw_rows(pg_ledger._table)
    statuses_by_hash = {}
    for row in rows:
        statuses_by_hash.setdefault(row["audit_hash"], []).append(row["status"])
    assert "reconciliation_aborted_non_idempotent" in statuses_by_hash["0xRECON_1"]
    assert "actuation_reconciled" in statuses_by_hash["0xRECON_2"]


# ---------------------------------------------------------------------------
# 4. Counterparty Gate Multi-Branch Tests
# ---------------------------------------------------------------------------

def test_counterparty_gate_all_three_branches_real_postgres(pg_ledger):
    from decision.contracts.adversarial_clearance import AdversarialClearanceStatus

    gate = CounterpartyModelGate(ledger_client=pg_ledger)

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

    rows = _fetch_raw_rows(pg_ledger._table)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# 5. End-to-End Internet Bill Scenario Against Real Postgres
# ---------------------------------------------------------------------------

def test_internet_bill_scenario_complete_closed_loop_real_postgres(pg_ledger):
    trace = run_internet_bill_scenario(ledger_client=pg_ledger)

    assert any("don't agree to a contract longer than 12 months" in c.lower() for c in trace.extracted_context.constraints)
    assert trace.first_pass_route == RouteType.BOUNCE
    assert trace.second_pass_route == RouteType.ACT_SILENTLY
    assert trace.terminal_dispatch_result["permitted"] is True
    assert len(trace.terminal_dispatch_result["actuations"]) == 1

    rows = _fetch_raw_rows(pg_ledger._table)
    view_names = [r["view_name"] for r in rows]
    assert view_names == [
        "historical_seed",
        "adversarial_counterparty_evaluation",
        "adversarial_counterparty_evaluation",
        "action_gate_evaluation",
        "action_gate_evaluation",
        "metric_outcome",
    ]
