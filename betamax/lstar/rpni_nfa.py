#!/usr/bin/env python3
"""
Modified RPNI with NFA

This module implements an NFA-based variant of RPNI that:
- Builds a PTA from positive examples (reusing PTA from rpni.py)
- Keeps a nondeterministic right-linear grammar (NFA) rather than determinizing
- Iteratively merges states in the NFA when consistent with negatives
- Checks consistency using NFA simulation (no regex conversion needed)
- Can export to a right-linear grammar compatible with ec_runtime

Exports:
    - rpni_nfa(positives, negatives) -> (nfa, start)
    - nfa_to_right_linear_grammar(nfa) -> (grammar, start_symbol, alphabet_list)
    - learn_grammar_from_samples_nfa(positives, negatives) -> (grammar, start_symbol, alphabet_list)

NFA representation:
    class NFA:
        start: int
        delta: Dict[int, Dict[str, Set[int]]]  # transitions
        accept: Set[int]
        alphabet: Set[str]

Grammar convention matches rpni.dfa_to_right_linear_grammar:
- Nonterminals: strings <Qi> for state i
- Terminals: single-character strings
- Epsilon: empty production []
"""
from __future__ import annotations
from typing import Dict, List, Set, Tuple, Iterable, Optional
from copy import deepcopy
from collections import deque

from .rpni import PTA  # Reuse PTA building and alphabet handling


class NFA:
    def __init__(self):
        self.start: int = 0
        self.delta: Dict[int, Dict[str, Set[int]]] = {}
        self.accept: Set[int] = set()
        self.alphabet: Set[str] = set()

    def states(self) -> Set[int]:
        s: Set[int] = set(self.delta.keys())
        for trans in self.delta.values():
            for ts in trans.values():
                s.update(ts)
        # Ensure all referenced targets exist in delta map
        for q in s:
            self.delta.setdefault(q, {})
        return s

    def accepts(self, w: str) -> bool:
        """NFA simulation without epsilon transitions."""
        current: Set[int] = {self.start}
        if not current:
            return False
        for a in w:
            nxt: Set[int] = set()
            for q in current:
                ts = self.delta.get(q, {}).get(a)
                if ts:
                    nxt.update(ts)
            current = nxt
            if not current:
                return False
        return any(q in self.accept for q in current)


def pta_to_nfa(pta: PTA) -> NFA:
    """Materialize PTA as an NFA (deterministic initially)."""
    nfa = NFA()
    nfa.start = 0
    nfa.alphabet = set(pta.alphabet)
    # Initialize states
    for v in range(len(pta.nodes)):
        nfa.delta[v] = {}
        if pta.nodes[v].accept:
            nfa.accept.add(v)
    # Transitions as singleton sets
    for v in range(len(pta.nodes)):
        for a, u in pta.nodes[v].next.items():
            nfa.delta[v].setdefault(a, set()).add(u)
            nfa.delta.setdefault(u, {})
    return nfa


def _redirect_all_incoming(delta: Dict[int, Dict[str, Set[int]]], src: int, dst: int) -> None:
    """Replace references to src with dst in all transition sets."""
    for q, trans in delta.items():
        for a, ts in trans.items():
            if src in ts:
                ts.discard(src)
                ts.add(dst)


def merge_to_nfa(nfa: NFA, i: int, j: int) -> Tuple[NFA, int]:
    """
    Merge state j into state i.
    - Redirect all incoming edges to j so they point to i
    - Union outgoing transitions: i's a-transitions gain all of j's a-targets
    - Accepting: i is accepting if either was accepting
    - Start: if start was j or i, start becomes i
    - Remove j from the transition map
    Returns (new_nfa, new_state) where new_state is the resulting merged state id (i).
    """
    if i == j:
        return deepcopy(nfa), i

    new = NFA()
    new.start = nfa.start
    new.alphabet = set(nfa.alphabet)
    new.delta = {q: {a: set(ts) for a, ts in trans.items()} for q, trans in nfa.delta.items()}
    new.accept = set(nfa.accept)

    # Ensure presence
    new.delta.setdefault(i, {})
    new.delta.setdefault(j, {})

    # Redirect incoming to j -> i
    _redirect_all_incoming(new.delta, j, i)

    # Union outgoing transitions from j into i
    for a, ts in list(new.delta[j].items()):
        new.delta[i].setdefault(a, set()).update(ts)

    # Remove j's state entry
    if j in new.delta:
        del new.delta[j]
    if j in new.accept:
        new.accept.add(i)
        new.accept.discard(j)

    # Fix start
    if new.start == j:
        new.start = i

    # Ensure all referenced target states exist in delta map
    for q, trans in list(new.delta.items()):
        for a, ts in trans.items():
            for t in ts:
                new.delta.setdefault(t, {})

    return new, i


def prune_unreachable(nfa: NFA) -> NFA:
    """Remove states unreachable from start; keep IDs as-is (no reindexing)."""
    reachable: Set[int] = set()
    q: deque[int] = deque([nfa.start])
    while q:
        s = q.popleft()
        if s in reachable:
            continue
        reachable.add(s)
        for ts in nfa.delta.get(s, {}).values():
            for t in ts:
                if t not in reachable:
                    q.append(t)
    new = NFA()
    new.start = nfa.start
    new.alphabet = set(nfa.alphabet)
    new.accept = set(x for x in nfa.accept if x in reachable)
    for s in reachable:
        new.delta[s] = {}
        for a, ts in nfa.delta.get(s, {}).items():
            kept = {t for t in ts if t in reachable}
            if kept:
                new.delta[s][a] = kept
    # Ensure map entries for all targets
    for s in list(new.delta.keys()):
        for ts in new.delta[s].values():
            for t in ts:
                new.delta.setdefault(t, {})
    return new


def rpni_nfa(positive_examples: Iterable[str], negative_examples: Iterable[str]) -> Tuple[NFA, int]:
    """
    Learn an NFA by merging PTA states while ensuring no negative is accepted.
    Follows the intuition in the provided pseudocode but uses NFA simulation
    instead of NFA->regex conversion for consistency checks.

    Returns (nfa, start_state).
    """
    # Build PTA
    pta = PTA()
    for w in positive_examples:
        pta.add_path(w, True)
    # Ensure alphabet includes negatives
    for w in negative_examples:
        for a in w:
            pta.alphabet.add(a)

    nfa = pta_to_nfa(pta)
    start = nfa.start

    # Iterative pairwise merging (greedy)
    changed = True
    while changed:
        changed = False
        states_list = list(nfa.states())  # snapshot for iteration
        # Deterministic ordering: larger id merges into smaller id to reduce churn
        states_list.sort()
        for ix in range(1, len(states_list)):
            for jx in range(ix):
                si, sj = states_list[ix], states_list[jx]
                if si == sj:
                    continue
                merged_nfa, new_state = merge_to_nfa(nfa, sj, si)  # merge si into sj (sj survives)
                # Negative consistency
                bad = False
                for neg in negative_examples:
                    if merged_nfa.accepts(neg):
                        bad = True
                        break
                if bad:
                    continue
                # Positive coverage
                for pos in positive_examples:
                    if not merged_nfa.accepts(pos):
                        bad = True
                        break
                if bad:
                    continue
                # Accept merge
                nfa = prune_unreachable(merged_nfa)
                start = nfa.start
                changed = True
                break
            if changed:
                break

    nfa = prune_unreachable(nfa)
    return nfa, start


def nfa_to_right_linear_grammar(nfa: NFA) -> Tuple[Dict[str, List[List[str]]], str, List[str]]:
    """
    Convert NFA to a right-linear CFG:
      <Qi> -> a <Qj> for each transition i --a--> j (for all j in delta[i][a])
      <Qi> -> [] for each accepting i
    Returns (grammar, start_symbol, alphabet_list)
    """
    g: Dict[str, List[List[str]]] = {}

    def NT(i: int) -> str:
        return f"<Q{i}>"

    # Ensure transitions map is normalized
    states = nfa.states()

    for i in states:
        nt = NT(i)
        g.setdefault(nt, [])
        if i in nfa.accept:
            g[nt].append([])  # epsilon
        for a, targets in nfa.delta.get(i, {}).items():
            for j in targets:
                g[nt].append([a, NT(j)])

    start_sym = NT(nfa.start)
    # Compute alphabet from terminals actually appearing in productions
    terms: Set[str] = set()
    for alts in g.values():
        for alt in alts:
            if alt:
                terms.add(alt[0])
    alphabet = sorted(terms)
    print(alphabet, g, start_sym)
    return g, start_sym, alphabet


def learn_grammar_from_samples_nfa(positives: Iterable[str], negatives: Iterable[str]) -> Tuple[Dict[str, List[List[str]]], str, List[str]]:
    """
    Convenience wrapper: learn NFA with modified RPNI and convert to grammar.
    """
    nfa, _ = rpni_nfa(positives, negatives)
    return nfa_to_right_linear_grammar(nfa)
