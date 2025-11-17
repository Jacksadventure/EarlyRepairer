#!/usr/bin/env python3
"""
RPNI variant with fuzzing-based consistency check.

This learner starts from the PTA (as in rpni.RPNI) but, instead of checking
merges only against the finite negative sample set, it additionally:

  * Materializes the current DFA hypothesis
  * Converts it to a right-linear grammar
  * Uses simplefuzzer.LimitFuzzer to generate samples from the hypothesis
  * Checks each generated sample with an external membership oracle

If any fuzzer-generated string is accepted by the DFA/grammar but *rejected*
by the external oracle, the merge is deemed inconsistent and rolled back.

Current learners (rpni.RPNI, rpni_nfa.rpni_nfa, L* variants) remain unchanged.
This module simply provides an additional learner that can be plugged into
repairer_lstar_ec.py as a new --learner option.
"""
from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Tuple, Optional
import os

import simplefuzzer as fuzzer

from .rpni import RPNI, DFA, dfa_to_right_linear_grammar

# Type alias for the external membership oracle. It should return True if the
# string is in the target language, False otherwise.
MembershipOracle = Callable[[str], bool]


class FuzzingRPNI(RPNI):
    """RPNI learner whose consistency check is fuzzing-based.

    The only behavioural difference from the base RPNI implementation is in
    the `_consistent_with_negatives` method:

      base:   ensure no known negative is accepted by the DFA
      this:   (1) ensure no known negative is accepted (same as base)
              (2) fuzz the DFA-as-grammar and ensure every generated sample
                  is accepted by the external membership oracle

    This mirrors the intent of "changing only is_consistent" in a typical
    RPNI-style learner while keeping the original learner untouched.
    """

    def __init__(
        self,
        positives: Iterable[str],
        negatives: Iterable[str],
        is_member: MembershipOracle,
        fuzz_samples: int = 10,
        fuzz_max_depth: int = 32,
    ) -> None:
        super().__init__(positives, negatives)
        self._is_member: MembershipOracle = is_member
        # Guard against silly values while allowing env/CLI tuning.
        try:
            fuzz_samples = int(fuzz_samples)
        except Exception:
            fuzz_samples = 10
        try:
            fuzz_max_depth = int(fuzz_max_depth)
        except Exception:
            fuzz_max_depth = 32
        self._fuzz_samples: int = max(0, fuzz_samples)
        self._fuzz_max_depth: int = max(1, fuzz_max_depth)

    # NOTE: We deliberately override _consistent_with_negatives so that the
    # existing _try_merge implementation in rpni.RPNI will pick up this
    # behaviour without any modification to the original learner.
    def _consistent_with_negatives(self, dfa: DFA) -> bool:  # type: ignore[override]
        """Check merge consistency using negatives + fuzzing.

        1) First delegate to the base implementation to enforce that *no*
           known negative example is accepted by the DFA.
        2) Then convert the DFA to a right-linear grammar and fuzz from it
           using simplefuzzer. Every generated sample must be accepted by the
           external membership oracle; otherwise the DFA is deemed invalid.
        """
        # Step 1: retain the original RPNI negative-consistency check.
        if not super()._consistent_with_negatives(dfa):
            return False

        # Step 2: fuzz-based consistency against external membership oracle.
        try:
            grammar, start_sym, _alphabet = dfa_to_right_linear_grammar(dfa)
        except Exception:
            # If we cannot even materialize a grammar, treat as inconsistent.
            return False
        if not grammar or start_sym not in grammar:
            return False

        try:
            gf = fuzzer.LimitFuzzer(grammar)
        except Exception:
            # If we cannot fuzz from the grammar, conservatively reject.
            return False

        for _ in range(self._fuzz_samples):
            try:
                s = gf.iter_fuzz(key=start_sym, max_depth=self._fuzz_max_depth)
            except Exception:
                # Stop fuzzing on internal fuzzer errors/timeouts.
                break
            if not isinstance(s, str):
                continue
            # Treat None/empty string like any other candidate; the external
            # oracle decides whether it is valid.
            try:
                ok = self._is_member(s)
            except Exception:
                # Defensive: if the oracle itself errors, skip this sample.
                continue
            if not ok:
                # DFA/grammar generated a counterexample rejected by oracle.
                return False

        return True


def learn_grammar_from_samples_fuzz(
    positives: Iterable[str],
    negatives: Iterable[str],
    is_member: MembershipOracle,
    fuzz_samples: Optional[int] = None,
    fuzz_max_depth: Optional[int] = None,
) -> Tuple[Dict[str, List[List[str]]], str, List[str]]:
    """Convenience wrapper:

        - Learn a DFA using FuzzingRPNI
        - Convert it to a right-linear grammar

    The fuzzing parameters can be controlled via function arguments or via
    environment variables:

        LSTAR_RPNI_FUZZ_SAMPLES   (default: 10)
        LSTAR_RPNI_FUZZ_MAX_DEPTH (default: 32)
    """
    if fuzz_samples is None:
        try:
            fuzz_samples = int(os.getenv("LSTAR_RPNI_FUZZ_SAMPLES", "10"))
        except Exception:
            fuzz_samples = 10
    if fuzz_max_depth is None:
        try:
            fuzz_max_depth = int(os.getenv("LSTAR_RPNI_FUZZ_MAX_DEPTH", "32"))
        except Exception:
            fuzz_max_depth = 32

    learner = FuzzingRPNI(
        positives=positives,
        negatives=negatives,
        is_member=is_member,
        fuzz_samples=fuzz_samples,
        fuzz_max_depth=fuzz_max_depth,
    )
    dfa = learner.learn()
    return dfa_to_right_linear_grammar(dfa)
