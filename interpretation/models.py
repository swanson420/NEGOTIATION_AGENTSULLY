"""
interpretation/models.py

Data contracts produced by pipeline interrogation stages.
"""

from typing import List

from pydantic import BaseModel, Field


class ExtractedContext(BaseModel):
    """
    Bounded state vector produced by ContextInterrogator.extract_context,
    grounded strictly in raw_input and ledger text across six channels.
    """
    objectives: List[str] = Field(default_factory=list)
    requirements: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    facts: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    contradictions: List[str] = Field(default_factory=list)
    is_truncated: bool = False
