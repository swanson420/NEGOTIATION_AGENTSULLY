"""
interpretation/context_interrogator.py

First-stage sensory interrogator.
Operates as a high-fidelity transducer converting raw textual input and ledger state
into an error-checked, bounded state vector (ExtractedContext) without introducing
ungrounded extrapolations.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

from .base import ContextInterrogatorStage
from .config import PipelineSystemConfig
from .models import ExtractedContext


class ContextInterrogator(ContextInterrogatorStage):
    """
    First-stage sensory interrogator.

    Operates as a high-fidelity transducer converting raw textual input and ledger state
    into an error-checked, bounded state vector (ExtractedContext) without introducing
    ungrounded extrapolations.
    """

    def __init__(self, config: Optional[PipelineSystemConfig] = None) -> None:
        self.config = config or PipelineSystemConfig()

    def extract_context(self, raw_input: str, ledger: str) -> ExtractedContext:
        """
        Extracts structured context strictly grounded in raw_input and ledger text.

        Evaluates signals across six distinct operational channels:
        1. Objectives: Direct goal declarations and imperatives.
        2. Requirements: Explicit functional stipulations ("must", "shall", "needs to").
        3. Constraints: Hard operational boundaries, resource caps, and negative requirements ("cannot", "must not", "don't", "limited to").
        4. Facts: Asserted domain statements and environmental baseline conditions.
        5. Dependencies: Referenced external systems, modules, libraries, or protocols.
        6. Contradictions: Direct oppositions detected within the input or against the ledger.
        """
        raw_text = raw_input.strip()
        ledger_text = ledger.strip()

        max_len = getattr(self.config, "max_context_chars", 32768)
        is_truncated = len(raw_text) > max_len or len(ledger_text) > max_len

        bounded_raw = raw_text[:max_len] if is_truncated else raw_text
        bounded_ledger = ledger_text[:max_len] if is_truncated else ledger_text

        sentences = self._segment_propositions(bounded_raw)
        ledger_sentences = self._segment_propositions(bounded_ledger) if bounded_ledger else []

        objectives: List[str] = []
        requirements: List[str] = []
        constraints: List[str] = []
        facts: List[str] = []
        dependencies: List[str] = []

        seen_objectives: Set[str] = set()
        seen_requirements: Set[str] = set()
        seen_constraints: Set[str] = set()
        seen_facts: Set[str] = set()
        seen_dependencies: Set[str] = set()

        for s in sentences:
            normalized = s.strip()
            if not normalized:
                continue

            deps = self._extract_dependencies(normalized)
            for dep in deps:
                if dep not in seen_dependencies:
                    seen_dependencies.add(dep)
                    dependencies.append(dep)

            c_type = self._classify_proposition(normalized)

            if c_type == "CONSTRAINT":
                if normalized not in seen_constraints:
                    seen_constraints.add(normalized)
                    constraints.append(normalized)
            elif c_type == "REQUIREMENT":
                if normalized not in seen_requirements:
                    seen_requirements.add(normalized)
                    requirements.append(normalized)
            elif c_type == "OBJECTIVE":
                if normalized not in seen_objectives:
                    seen_objectives.add(normalized)
                    objectives.append(normalized)
            elif c_type == "FACT":
                if normalized not in seen_facts:
                    seen_facts.add(normalized)
                    facts.append(normalized)

        contradictions = self._detect_contradictions(sentences, ledger_sentences)

        max_items = getattr(self.config, "max_extracted_items_per_channel", None)
        if max_items is not None:
            objectives = objectives[:max_items]
            requirements = requirements[:max_items]
            constraints = constraints[:max_items]
            facts = facts[:max_items]
            dependencies = dependencies[:max_items]
            contradictions = contradictions[:max_items]

        return ExtractedContext(
            objectives=objectives,
            requirements=requirements,
            constraints=constraints,
            facts=facts,
            dependencies=dependencies,
            contradictions=contradictions,
            is_truncated=is_truncated,
        )

    # -------------------------------------------------------------------------
    # Internal Parsing and Analytical Machinery
    # -------------------------------------------------------------------------

    def _segment_propositions(self, text: str) -> List[str]:
        """Splits text into discrete, evaluable clauses and sentences."""
        if not text:
            return []

        raw_lines = re.split(r"[\n\r]+", text)
        segments: List[str] = []

        for line in raw_lines:
            cleaned = re.sub(r"^(\s*[-*\u2022]\s*|\s*\d+\.\s*)", "", line).strip()
            if not cleaned:
                continue

            clauses = re.split(r"(?<=[.?!;])\s+", cleaned)
            for clause in clauses:
                clause_clean = clause.strip().rstrip(";").strip()
                if clause_clean:
                    segments.append(clause_clean)

        return segments

    def _classify_proposition(self, text: str) -> str:
        """
        Classifies an isolated proposition into exactly one channel based strictly
        on explicit linguistic anchors, preventing ungrounded extrapolations.
        """
        lower = text.lower()

        # Hard constraints & negative requirements (includes explicit contraction 'don't')
        constraint_patterns = [
            r"\b(must not|cannot|shall not|do not|don't|never|prohibited|forbidden|restricted to|limited to|max(imum)?\b|no more than|no fewer than|at most|without using)\b",
            r"\b(boundary|hard ceiling|cap of|strictly bounded by)\b",
        ]
        if any(re.search(p, lower) for p in constraint_patterns):
            return "CONSTRAINT"

        req_patterns = [
            r"\b(must|shall|needs to|required to|should|ensure that|responsible for|has to|mandates?)\b",
            r"\b(acceptance criteria|specifies that)\b",
        ]
        if any(re.search(p, lower) for p in req_patterns):
            return "REQUIREMENT"

        objective_patterns = [
            r"\b(goal is to|objective is to|aims? to|intent is to|purpose is to|wants? to|build|implement|create|design|generate|produce)\b",
            r"^(please\s+)?(build|make|extract|derive|compute|calculate|solve|construct|refactor|deploy|try\s+to|negotiate)\b",
        ]
        if any(re.search(p, lower) for p in objective_patterns):
            return "OBJECTIVE"

        fact_patterns = [
            r"\b(is|are|was|were|has been|currently|already|exists|defined as|runs on|configured as|jumped from)\b",
        ]
        if any(re.search(p, lower) for p in fact_patterns):
            return "FACT"

        return "FACT"

    def _extract_dependencies(self, text: str) -> List[str]:
        """Identifies explicit external modules, packages, file paths, protocols, and services."""
        dependencies: List[str] = []

        patterns = [
            r"(?:from|import|require|using|depends on|integrated with|adapter into|connects to)\s+([a-zA-Z0-9_\-\./]+)",
            r"\b([a-zA-Z0-9_\-]+\.(?:py|json|yaml|yml|md|sql|proto|rs|go|ts|js))\b",
            r"\b(https?://[^\s]+|postgres(?:ql)?|redis|grpc|rest|kafka|docker|k8s|s3)\b",
        ]
        for p in patterns:
            matches = re.findall(p, text, flags=re.IGNORECASE)
            for m in matches:
                dep_name = m.strip("`'\".,;()")
                if len(dep_name) > 1 and not dep_name.lower() in {"a", "an", "the", "and", "or", "to", "in"}:
                    dependencies.append(dep_name)

        return dependencies

    def _detect_contradictions(self, input_sentences: List[str], ledger_sentences: List[str]) -> List[str]:
        """
        Compares statements across the input stream and against the ledger state
        to identify explicit opposing polarity or mutual exclusivity.
        """
        contradictions: List[str] = []
        all_pairs: List[Tuple[str, str, str]] = []

        for i in range(len(input_sentences)):
            for j in range(i + 1, len(input_sentences)):
                all_pairs.append((input_sentences[i], input_sentences[j], "input vs input"))

        for s_in in input_sentences:
            for s_led in ledger_sentences:
                all_pairs.append((s_in, s_led, "input vs ledger"))

        for s1, s2, context_type in all_pairs:
            conflict = self._evaluate_polarity_conflict(s1, s2)
            if conflict:
                contradiction_entry = f"[{context_type}] '{s1}' contradicts '{s2}' ({conflict})"
                if contradiction_entry not in contradictions:
                    contradictions.append(contradiction_entry)

        return contradictions

    def _evaluate_polarity_conflict(self, stmt1: str, stmt2: str) -> Optional[str]:
        """Evaluates whether two statements form a direct logical negation on a shared subject."""
        w1 = set(re.findall(r"\w+", stmt1.lower()))
        w2 = set(re.findall(r"\w+", stmt2.lower()))

        stop_words = {"the", "a", "an", "is", "are", "to", "of", "and", "in", "that", "this", "it", "for", "with", "as"}
        content_overlap = (w1 & w2) - stop_words
        if len(content_overlap) < 2:
            return None

        negations = {"not", "never", "no", "cannot", "must not", "shall not", "without", "prohibited", "bypasses", "don't"}
        has_neg1 = any(n in stmt1.lower() for n in negations)
        has_neg2 = any(n in stmt2.lower() for n in negations)

        if has_neg1 != has_neg2:
            return "direct polarity negation on shared entity"

        antonym_pairs = [
            ("enable", "disable"),
            ("allow", "disallow"),
            ("allow", "block"),
            ("sync", "async"),
            ("blocking", "non-blocking"),
            ("bypass", "enforce"),
            ("strict", "loose"),
            ("frozen", "mutable"),
        ]
        for a, b in antonym_pairs:
            if (a in stmt1.lower() and b in stmt2.lower()) or (b in stmt1.lower() and a in stmt2.lower()):
                return f"mutually exclusive directive ({a} vs {b})"

        return None
