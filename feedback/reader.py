"""
Telemetry Ledger Reader.

Strictly bounded to verified ledger/ledger.py query primitives:
  - `get_event(event_id)`
  - `verify_provenance(event_ids, expected_hash)`

Does not invent unbuilt query-by-audit-hash indices or hallucinate methods on the storage engine.
"""

from typing import Any, Dict, List, Optional
from ledger.adapter import LedgerContractError


class TelemetryReader:
    """Read-only accessor verified against real ledger/ledger.py capabilities."""

    def __init__(self, ledger_client: Any):
        if not hasattr(ledger_client, "get_event") or not callable(ledger_client.get_event):
            raise LedgerContractError("Ledger client must implement callable 'get_event(event_id)'.")
        self._ledger = ledger_client

    def get_metric_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Directly retrieves a single telemetry event by event_id from the ledger.
        """
        if not event_id or not isinstance(event_id, str):
            raise ValueError("event_id must be a non-empty string.")
        return self._ledger.get_event(event_id)

    def verify_telemetry_provenance(self, event_ids: List[str], expected_hash: str) -> bool:
        """
        Verifies cryptographic provenance and hash integrity of an event sequence
        via the ledger's verification interface.
        """
        if not hasattr(self._ledger, "verify_provenance") or not callable(self._ledger.verify_provenance):
            raise LedgerContractError("Ledger client lacks callable 'verify_provenance(event_ids, expected_hash)'.")
        if not isinstance(event_ids, list) or not event_ids:
            raise ValueError("event_ids must be a non-empty list of event identifiers.")
        return bool(self._ledger.verify_provenance(event_ids, expected_hash))
