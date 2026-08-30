"""
interpretation/config.py

Runtime tuning knobs for pipeline interrogation stages.
"""

from typing import Optional

from pydantic import BaseModel


class PipelineSystemConfig(BaseModel):
    """
    Bounds applied by ContextInterrogator when extracting context.

    max_context_chars: hard cap on raw_input/ledger length before truncation.
    max_extracted_items_per_channel: cap on entries kept per channel; None means unbounded.
    """
    max_context_chars: int = 32768
    max_extracted_items_per_channel: Optional[int] = None
