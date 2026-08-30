"""
interpretation/base.py

Base contract for pipeline interrogation stages.
"""

from abc import ABC, abstractmethod

from .models import ExtractedContext


class ContextInterrogatorStage(ABC):
    """
    Contract for a stage that transduces raw textual input and ledger state
    into an error-checked, bounded ExtractedContext.
    """

    @abstractmethod
    def extract_context(self, raw_input: str, ledger: str) -> ExtractedContext:
        ...
