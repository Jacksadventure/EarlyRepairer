"""
Sample-based Teacher for L* using positive and negative example sets.

This teacher answers membership queries by consulting provided positive/negative
sets. For strings not present in either set, it applies a chosen policy
(default: treat as negative). Equivalence is checked against the finite sets:
all positives must be accepted and all negatives must be rejected. If a mismatch
is found, a counterexample is returned.

API:
- SampleTeacher(positives: set[str], negatives: set[str], unknown_policy: str = "negative")
    unknown_policy in {"negative", "positive", "error"}
      - "negative": strings not in positives/negatives are treated as non-members (0)
      - "positive": strings not in positives/negatives are treated as members (1)
      - "error":    encountering unknown strings in is_member raises an error

Implements:
- is_member(q) -> 0/1
- is_equivalent(grammar, start) -> (bool, counterexample_or_None)
"""

from typing import Dict, List, Tuple, Optional, Set
import earleyparser


class SampleTeacher:
    def __init__(self, positives: Set[str], negatives: Set[str], unknown_policy: str = "negative"):
        self.positives = set(positives)
        self.negatives = set(negatives)
        if unknown_policy not in {"negative", "positive", "error"}:
            raise ValueError("unknown_policy must be one of {'negative','positive','error'}")
        self.unknown_policy = unknown_policy

    # Membership query
    def is_member(self, q: str) -> int:
        if q in self.positives:
            return 1
        if q in self.negatives:
            return 0
        if self.unknown_policy == "negative":
            return 0
        if self.unknown_policy == "positive":
            return 1
        raise KeyError(f"Unknown membership for string: {repr(q)}")

    # Finite-set "equivalence": grammar must accept all positives and reject all negatives.
    def is_equivalent(self, grammar: Dict[str, List[List[str]]], start: str) -> Tuple[bool, Optional[str]]:
        parser = earleyparser.EarleyParser(grammar)
        # Check positives: all must be accepted
        for p in self.positives:
            try:
                list(parser.recognize_on(p, start))
            except Exception:
                return False, p  # positive not accepted -> counterexample
        # Check negatives: none must be accepted
        for n in self.negatives:
            try:
                list(parser.recognize_on(n, start))
                return False, n  # negative accepted -> counterexample
            except Exception:
                pass
        return True, None
