#!/usr/bin/env python3
"""\
RPNI variant with cross-over based consistency check.

This learner starts from the PTA (as in rpni.RPNI) but, instead of using
random fuzzing from the learned grammar, it validates each merge by
constructing *cross-over* examples from the original positive samples.

Intuition (per your description):

  If we are merging two states that are reached by prefixes of two
  different positive examples, e.g.

    abcdefg  (merge happens after consuming 'd')
    pqrstuv  (merge happens after consuming 'u')

  then we can cross over at that state to form new strings like:

    abc[d]efg  +  pqrst[u]v  ->  abcuv, pqrstdefg

  These cross-overs are plausible candidates that the merged DFA might
  start accepting; we then rely on the external membership oracle on
  these new strings. If any such cross-over is rejected by the oracle
  while being accepted by the hypothesis DFA, the merge is deemed
  inconsistent and rolled back.

This module mirrors the structure of rpni_fuzz: we subclass RPNI and
only override the consistency check (here implemented as a custom
_cross_over_consistency inside _consistent_with_negatives). Existing
learners remain untouched; betamax.py can import this as an
additional --learner option (e.g. 'rpni_xover').
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Tuple, Optional
import os

from .rpni import RPNI, DFA, dfa_to_right_linear_grammar

# External membership oracle: True if string is in the target language.
MembershipOracle = Callable[[str], bool]


class XoverRPNI(RPNI):
    """RPNI learner whose merge consistency is checked via cross-over.

    Behaviour relative to base RPNI:
      - still enforces that no known negative is accepted (original
        _consistent_with_negatives)
      - additionally, generates cross-over examples from positive
        samples under the current DFA hypothesis and rejects merges
        that introduce oracle-rejected strings.
    """

    def __init__(
        self,
        positives: Iterable[str],
        negatives: Iterable[str],
        is_member: MembershipOracle,
        max_pairs: int = 50,
        max_positions: int = 10,
    ) -> None:
        super().__init__(positives, negatives)
        self._is_member: MembershipOracle = is_member
        # Snapshot positives as a list (for deterministic iteration)
        self._positives: List[str] = [p for p in positives if isinstance(p, str)]
        try:
            max_pairs = int(max_pairs)
        except Exception:
            max_pairs = 50
        try:
            max_positions = int(max_positions)
        except Exception:
            max_positions = 10
        self._max_pairs: int = max(0, max_pairs)
        self._max_positions: int = max(1, max_positions)

    # NOTE: we override _consistent_with_negatives so that the existing
    # _try_merge implementation in rpni.RPNI picks up our extra
    # cross-over based consistency without touching the original code.
    def _consistent_with_negatives(self, dfa: DFA) -> bool:  # type: ignore[override]
        """Check merge consistency using negatives + cross-over.

        Steps:
          1) Delegate to the base implementation to ensure no known
             negative example is accepted by the DFA.
          2) Generate cross-over candidates from pairs of *positive*
             samples according to the current DFA structure and reject
             the DFA if any such candidate is rejected by the external
             membership oracle while being accepted by the DFA.
        """
        # Step 1: original negative-set consistency.
        if not super()._consistent_with_negatives(dfa):
            return False

        # Step 2: cross-over consistency using positives + oracle.
        return self._cross_over_consistency(dfa)

    # ------------------------------------------------------------------
    # Cross-over core
    # ------------------------------------------------------------------

    def _cross_over_consistency(self, dfa: DFA) -> bool:
        """Return True if no cross-over counterexample is found.

        Implementation details:
          - enumerate up to self._max_pairs pairs of positives (p, q)
          - for each pair, compute DFA state traces along both strings
          - for positions where DFA states match, form two cross-over
            strings (prefix_p + suffix_q, prefix_q + suffix_p)
          - if DFA accepts a cross-over but the external oracle rejects
            it, treat as a counterexample and return False.

        We bound the search using environment variables:

          LSTAR_RPNI_XOVER_PAIRS     (default: self._max_pairs)
          LSTAR_RPNI_XOVER_POSITIONS (default: self._max_positions)
        """
        # Early exit: not enough positives to form pairs.
        if len(self._positives) < 2 or self._max_pairs <= 0:
            return True

        # Read optional env-based limits (if available).
        max_pairs = self._max_pairs
        try:
            env_pairs = os.getenv("LSTAR_RPNI_XOVER_PAIRS")
            if env_pairs is not None:
                max_pairs = max(0, int(env_pairs))
        except Exception:
            pass

        max_positions = self._max_positions
        try:
            env_pos = os.getenv("LSTAR_RPNI_XOVER_POSITIONS")
            if env_pos is not None:
                max_positions = max(1, int(env_pos))
        except Exception:
            pass

        def dfa_trace(w: str) -> List[Optional[int]]:
            """Return list of DFA states after each consumed symbol.

            If we hit a missing transition, the remainder is filled with
            None (meaning path is dead for those suffixes).
            """
            states: List[Optional[int]] = []
            q = dfa.start
            for ch in w:
                nxt = dfa.delta[q].get(ch)
                if nxt is None:
                    states.append(None)
                    # This path is dead beyond this point
                    q = -1
                    break
                q = nxt
                states.append(q)
            return states

        def dfa_accepts(w: str) -> bool:
            q = dfa.start
            for ch in w:
                nxt = dfa.delta[q].get(ch)
                if nxt is None:
                    return False
                q = nxt
            return dfa.accept[q]

        # Limit how many positives we even consider.
        pos_list = self._positives
        if not pos_list:
            return True

        pair_count = 0
        seen_cross: set[str] = set()

        # Deterministic ordering: use prefix of pos_list; we walk pairs
        # (i, j), i < j, and stop when pair_count reaches max_pairs.
        n = len(pos_list)
        for i in range(n):
            if pair_count >= max_pairs:
                break
            p = pos_list[i]
            if not isinstance(p, str) or not p:
                continue
            trace_p = dfa_trace(p)
            if not trace_p:
                continue
            max_i = min(len(trace_p), max_positions)
            for j in range(i + 1, n):
                if pair_count >= max_pairs:
                    break
                q = pos_list[j]
                if not isinstance(q, str) or not q:
                    continue
                trace_q = dfa_trace(q)
                if not trace_q:
                    continue
                max_j = min(len(trace_q), max_positions)

                pair_count += 1

                # Consider positions where DFA states match and are not None
                for idx_p in range(max_i):
                    sp = trace_p[idx_p]
                    if sp is None:
                        continue
                    for idx_q in range(max_j):
                        sq = trace_q[idx_q]
                        if sq is None or sq != sp:
                            continue
                        # Cross-over at positions idx_p, idx_q (inclusive)
                        c1 = p[: idx_p + 1] + q[idx_q + 1 :]
                        c2 = q[: idx_q + 1] + p[idx_p + 1 :]

                        for cand in (c1, c2):
                            if not cand or cand in seen_cross:
                                continue
                            seen_cross.add(cand)

                            # Only interesting if DFA accepts it; then
                            # oracle must also accept, otherwise this
                            # merge introduced a false positive.
                            try:
                                dfa_ok = dfa_accepts(cand)
                            except Exception:
                                dfa_ok = False
                            if not dfa_ok:
                                continue

                            try:
                                oracle_ok = self._is_member(cand)
                            except Exception:
                                # Defensive: if oracle errors, skip.
                                continue
                            if not oracle_ok:
                                # Counterexample found: reject merge.
                                return False

        return True


def learn_grammar_from_samples_xover(
    positives: Iterable[str],
    negatives: Iterable[str],
    is_member: MembershipOracle,
    max_pairs: Optional[int] = None,
    max_positions: Optional[int] = None,
) -> Tuple[Dict[str, List[List[str]]], str, List[str]]:
    """Convenience wrapper:

        - Learn a DFA using XoverRPNI
        - Convert it to a right-linear grammar

    Cross-over exploration limits can be controlled via arguments or
    environment variables:

        LSTAR_RPNI_XOVER_PAIRS     (default: 50)
        LSTAR_RPNI_XOVER_POSITIONS (default: 10)
    """
    if max_pairs is None:
        try:
            max_pairs = int(os.getenv("LSTAR_RPNI_XOVER_PAIRS", "50"))
        except Exception:
            max_pairs = 50
    if max_positions is None:
        try:
            max_positions = int(os.getenv("LSTAR_RPNI_XOVER_POSITIONS", "10"))
        except Exception:
            max_positions = 10

    learner = XoverRPNI(
        positives=positives,
        negatives=negatives,
        is_member=is_member,
        max_pairs=max_pairs,
        max_positions=max_positions,
    )
    dfa = learner.learn()
    return dfa_to_right_linear_grammar(dfa)
