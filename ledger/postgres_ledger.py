"""
ledger/postgres_ledger.py

Postgres-backed implementation of the ledger contract consumed by
ledger/adapter.py: any client exposing record_decision_view(record: Any) -> bool.

Scope is deliberately narrow: exact column mapping for the fields
ledger/adapter.py's LedgerAuditEnvelope always produces (audit_hash,
view_name, timestamp, optional route), a JSONB column for everything
else, idempotent DDL, and a psycopg2 connection lifecycle managed by
this class alone (open at construction, reused across calls, no pool).
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

logger = logging.getLogger(__name__)

_KNOWN_COLUMNS = ("audit_hash", "view_name", "route", "timestamp")


class PostgresLedgerConnectionError(RuntimeError):
    """Raised at construction when DATABASE_URL is missing or the connection/DDL setup fails."""
    pass


class PostgresLedger:
    """
    Real ledger backing store. Implements the exact duck-typed contract
    ledger/adapter.py and action/action_gate.py already call against:
    record_decision_view never raises -- it returns True on a durable
    insert and False on any failure, logging the failure rather than
    discarding it.
    """

    def __init__(self, dsn: Optional[str] = None, table_name: str = "ledger_decision_views"):
        resolved_dsn = dsn or os.environ.get("DATABASE_URL")
        if not resolved_dsn:
            raise PostgresLedgerConnectionError(
                "PostgresLedger requires a DSN: pass dsn= explicitly or set DATABASE_URL."
            )

        self._dsn = resolved_dsn
        self._table = table_name

        try:
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = False
            self._ensure_schema()
        except Exception as exc:
            raise PostgresLedgerConnectionError(
                f"PostgresLedger failed to connect or initialize schema: {exc}"
            ) from exc

    # -------------------------------------------------------------------
    # Schema
    # -------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Idempotent DDL: safe to run on every construction, including concurrently."""
        create_table = sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {table} (
                id BIGSERIAL PRIMARY KEY,
                audit_hash TEXT NOT NULL,
                view_name TEXT NOT NULL,
                route TEXT,
                "timestamp" TIMESTAMPTZ NOT NULL,
                payload JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        ).format(table=sql.Identifier(self._table))

        create_audit_hash_index = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON {table} (audit_hash)"
        ).format(
            name=sql.Identifier(f"{self._table}_audit_hash_idx"),
            table=sql.Identifier(self._table),
        )

        create_reconciliation_index = sql.SQL(
            "CREATE INDEX IF NOT EXISTS {name} ON {table} (view_name, audit_hash, recorded_at)"
        ).format(
            name=sql.Identifier(f"{self._table}_view_audit_recorded_idx"),
            table=sql.Identifier(self._table),
        )

        with self._conn.cursor() as cur:
            cur.execute(create_table)
            cur.execute(create_audit_hash_index)
            cur.execute(create_reconciliation_index)
        self._conn.commit()

    # -------------------------------------------------------------------
    # Connection lifecycle
    # -------------------------------------------------------------------

    def _ensure_connection(self) -> None:
        """Reconnects a dropped connection. No pooling -- one connection, lazily revived."""
        if self._conn.closed:
            logger.warning("PostgresLedger connection was closed; reconnecting.")
            self._conn = psycopg2.connect(self._dsn)
            self._conn.autocommit = False

    def close(self) -> None:
        try:
            if not self._conn.closed:
                self._conn.close()
        except Exception:
            logger.exception("PostgresLedger failed to close connection cleanly.")

    def __enter__(self) -> "PostgresLedger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -------------------------------------------------------------------
    # Contract: record_decision_view(record) -> bool, never raises
    # -------------------------------------------------------------------

    def record_decision_view(self, record: Any) -> bool:
        if not isinstance(record, dict):
            return False
        if "audit_hash" not in record or "view_name" not in record:
            return False

        audit_hash = record["audit_hash"]
        view_name = record["view_name"]
        route = record.get("route")

        try:
            timestamp = self._coerce_timestamp(record.get("timestamp"))
        except (TypeError, ValueError) as exc:
            logger.error(
                "PostgresLedger rejected record with unparseable timestamp "
                "[audit_hash=%s view_name=%s]: %s",
                audit_hash, view_name, exc,
            )
            return False

        payload = {k: v for k, v in record.items() if k not in _KNOWN_COLUMNS}

        insert = sql.SQL(
            "INSERT INTO {table} (audit_hash, view_name, route, \"timestamp\", payload) "
            "VALUES (%s, %s, %s, %s, %s)"
        ).format(table=sql.Identifier(self._table))

        try:
            self._ensure_connection()
            with self._conn.cursor() as cur:
                cur.execute(insert, (audit_hash, view_name, route, timestamp, Json(payload)))
            self._conn.commit()
            return True
        except Exception as exc:
            logger.error(
                "PostgresLedger insert failed [audit_hash=%s view_name=%s]: %s",
                audit_hash, view_name, exc, exc_info=True,
            )
            try:
                self._conn.rollback()
            except Exception:
                logger.exception(
                    "PostgresLedger rollback failed after insert error [audit_hash=%s]", audit_hash
                )
            return False

    @staticmethod
    def _coerce_timestamp(value: Any) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise TypeError(f"Unsupported timestamp type: {type(value).__name__}")

    # -------------------------------------------------------------------
    # Read paths consumed elsewhere in the codebase (ActuationReconciler,
    # and get_event for parity with ConformingRealLedger). Never raise;
    # a failed read returns an empty/None result and logs, same as writes.
    # -------------------------------------------------------------------

    def get_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        query = sql.SQL(
            "SELECT audit_hash, view_name, route, \"timestamp\", payload FROM {table} "
            "WHERE audit_hash = %s ORDER BY recorded_at ASC LIMIT 1"
        ).format(table=sql.Identifier(self._table))

        try:
            self._ensure_connection()
            with self._conn.cursor() as cur:
                cur.execute(query, (event_id,))
                row = cur.fetchone()
        except Exception:
            logger.exception("PostgresLedger get_event query failed [audit_hash=%s]", event_id)
            self._safe_rollback()
            return None

        if row is None:
            return None

        audit_hash, view_name, route, timestamp, payload = row
        record: Dict[str, Any] = {
            "audit_hash": audit_hash,
            "view_name": view_name,
            "timestamp": timestamp.isoformat(),
        }
        if route is not None:
            record["route"] = route
        record.update(payload or {})
        return record

    def get_uncommitted_views(self, view_name: str, older_than_seconds: float) -> List[Dict[str, Any]]:
        """
        Returns the latest row per audit_hash for view_name where that latest
        row is still 'intent_recorded' and older than older_than_seconds --
        i.e. no subsequent write (actuation_committed, dispatch_failed, ...)
        ever superseded it. Shaped for action/reconciliation.py's
        StalledActuationRecord.

        Note: decision_record is reconstructed only when the stored payload
        carries 'action' and 'context' -- ActionGate's current audit payload
        does not, so real production rows will surface as
        decision_record=None. Reconciliation's fault-isolated path already
        treats that safely (a non-idempotent, non-replayable hold), which is
        the correct default when there isn't enough persisted state to
        replay an actuation blind.
        """
        query = sql.SQL(
            """
            WITH ranked AS (
                SELECT
                    audit_hash, route, "timestamp", payload, recorded_at,
                    ROW_NUMBER() OVER (PARTITION BY audit_hash ORDER BY recorded_at DESC) AS rn
                FROM {table}
                WHERE view_name = %s
            )
            SELECT audit_hash, route, "timestamp", payload, recorded_at,
                   EXTRACT(EPOCH FROM (now() - recorded_at)) AS elapsed_seconds
            FROM ranked
            WHERE rn = 1
              AND payload->>'status' = 'intent_recorded'
              AND recorded_at < now() - (%s * interval '1 second')
            """
        ).format(table=sql.Identifier(self._table))

        try:
            self._ensure_connection()
            with self._conn.cursor() as cur:
                cur.execute(query, (view_name, older_than_seconds))
                rows = cur.fetchall()
        except Exception:
            logger.exception(
                "PostgresLedger get_uncommitted_views query failed [view_name=%s]", view_name
            )
            self._safe_rollback()
            return []

        results: List[Dict[str, Any]] = []
        for audit_hash, route, timestamp, payload, recorded_at, elapsed_seconds in rows:
            payload = payload or {}
            results.append({
                "audit_hash": audit_hash,
                "decision_record": self._reconstruct_decision_record(audit_hash, payload),
                "dispatch_target": payload.get("dispatch_target", route),
                "timestamp": recorded_at,
                "elapsed_seconds": float(elapsed_seconds),
            })
        return results

    @staticmethod
    def _reconstruct_decision_record(audit_hash: str, payload: Dict[str, Any]) -> Optional[Any]:
        if "action" not in payload or "context" not in payload:
            return None
        try:
            from decision.contracts.decision_package import DecisionRecord
            return DecisionRecord(
                audit_hash=audit_hash,
                action=payload["action"],
                context=payload["context"],
                recursion_depth=payload.get("recursion_depth", 0),
            )
        except Exception:
            logger.exception(
                "PostgresLedger could not reconstruct DecisionRecord [audit_hash=%s]", audit_hash
            )
            return None

    def _safe_rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:
            logger.exception("PostgresLedger rollback failed.")
