#!/usr/bin/env python3
"""
RPNI (Blue-Fringe) DFA learner from positive and negative samples.

- Builds a PTA from positives
- Ensures alphabet covers symbols seen in negatives
- Iteratively merges BLUE into RED when consistent with negatives
- Exposes:
    * class DFA
    * class RPNI
    * dfa_to_right_linear_grammar(dfa) -> (grammar, start_sym)

Grammar convention:
- Nonterminals are strings in angle brackets, e.g. '<Q0>'
- Terminals are single-character strings
- Epsilon is the empty production []

This grammar can be passed into ec_runtime.augment_grammar_ex for
error-correcting Earley parsing.
"""
from __future__ import annotations
from typing import Dict, List, Set, Tuple, Iterable, Optional
from collections import deque

class PTA:
    class Node:
        __slots__ = ("id", "accept", "next", "parent", "via")
        def __init__(self, id: int):
            self.id: int = id
            self.accept: bool = False
            self.next: Dict[str, int] = {}
            self.parent: int = -1
            self.via: str = ""

    def __init__(self):
        self.nodes: List[PTA.Node] = [PTA.Node(0)]
        self.alphabet: Set[str] = set()

    def add_path(self, w: str, is_positive: bool) -> int:
        s = 0
        for a in w:
            self.alphabet.add(a)
            if a not in self.nodes[s].next:
                nid = len(self.nodes)
                self.nodes.append(PTA.Node(nid))
                self.nodes[nid].parent = s
                self.nodes[nid].via = a
                self.nodes[s].next[a] = nid
                s = nid
            else:
                s = self.nodes[s].next[a]
        if is_positive:
            self.nodes[s].accept = True
        return s

class DFA:
    def __init__(self):
        self.start: int = 0
        self.delta: List[Dict[str, int]] = []
        self.accept: List[bool] = []
        self.alphabet: Set[str] = set()

    def accepts(self, w: str) -> bool:
        q = self.start
        for a in w:
            nxt = self.delta[q].get(a)
            if nxt is None:
                return False
            q = nxt
        return self.accept[q]

def complete(dfa: DFA) -> None:
    """Add a sink state for missing edges over the known alphabet."""
    n = len(dfa.delta)
    if not dfa.alphabet:
        return
    need = any(any(a not in dfa.delta[s] for a in dfa.alphabet) for s in range(n))
    if not need:
        return
    sink = n
    dfa.delta.append({})
    dfa.accept.append(False)
    for a in dfa.alphabet:
        dfa.delta[sink][a] = sink
    for s in range(n):
        for a in dfa.alphabet:
            if a not in dfa.delta[s]:
                dfa.delta[s][a] = sink

class RPNI:
    def __init__(self, positives: Iterable[str], negatives: Iterable[str]):
        self.pta = PTA()
        self.negatives: List[str] = list(negatives)
        # Build PTA on positives
        for w in positives:
            self.pta.add_path(w, True)
        # Ensure alphabet includes symbols from negatives
        for w in self.negatives:
            for a in w:
                self.pta.alphabet.add(a)

    @staticmethod
    def _find(rep: List[int], v: int) -> int:
        r = v
        while rep[r] != r:
            r = rep[r]
        return r

    def _materialize(self, rep: List[int], do_complete: bool = True) -> DFA:
        # Canonicalize reps
        n = len(self.pta.nodes)
        canon = rep[:]
        for v in range(n):
            while canon[v] != canon[canon[v]]:
                canon[v] = canon[canon[v]]
        # Map root -> new id
        idmap: Dict[int, int] = {}
        idc = 0
        for v in range(n):
            r = canon[v]
            if r not in idmap:
                idmap[r] = idc
                idc += 1
        dfa = DFA()
        dfa.delta = [dict() for _ in range(idc)]
        dfa.accept = [False] * idc
        dfa.alphabet = set(self.pta.alphabet)
        # Accepting
        for v in range(n):
            r = idmap[canon[v]]
            if self.pta.nodes[v].accept:
                dfa.accept[r] = True
        # Transitions
        for v in range(n):
            r = idmap[canon[v]]
            for a, u in self.pta.nodes[v].next.items():
                ru = idmap[canon[u]]
                # Deterministic by homomorphic propagation
                dfa.delta[r][a] = ru
        dfa.start = idmap[canon[0]]
        if do_complete:
            complete(dfa)
        return dfa

    def _consistent_with_negatives(self, dfa: DFA) -> bool:
        return all(not dfa.accepts(w) for w in self.negatives)

    def _try_merge(self, rep_in: List[int], qr: int, qb: int) -> Optional[List[int]]:
        """Attempt to merge qb into qr; return new rep if consistent, else None."""
        rep = rep_in[:]
        rep[qb] = qr
        Q = deque()
        Q.append((qr, qb))
        # Homomorphic propagation over PTA structure
        while Q:
            x, y = Q.popleft()
            # For every symbol in alphabet, align children
            for a in self.pta.alphabet:
                nx = self.pta.nodes[x].next.get(a)
                ny = self.pta.nodes[y].next.get(a)
                if ny is None:
                    continue
                if nx is None:
                    # After merge, class of y's child will be reachable from class(x) by 'a' via materialization.
                    # No immediate constraint needed.
                    continue
                rx = self._find(rep, nx)
                ry = self._find(rep, ny)
                if rx != ry:
                    # merge ry into rx
                    rep[ry] = rx
                    Q.append((rx, ry))
        # Check negatives
        dfa = self._materialize(rep)
        if not self._consistent_with_negatives(dfa):
            return None
        return rep

    def learn(self) -> DFA:
        N = len(self.pta.nodes)
        # Each node is its own rep initially
        rep = list(range(N))
        RED: Set[int] = set()
        BLUE: Set[int] = set()
        def add_blue_of(r: int):
            for _, v in self.pta.nodes[r].next.items():
                if v not in RED:
                    BLUE.add(v)
        RED.add(0)
        add_blue_of(0)

        while BLUE:
            qb = next(iter(BLUE))
            BLUE.remove(qb)
            merged = False
            for qr in list(RED):
                rep_try = self._try_merge(rep, qr, qb)
                if rep_try is not None:
                    # Commit: perform the same merges deterministically
                    rep = rep_try
                    # Expand RED frontier: merging qb into qr doesn't add new red id,
                    # but we should continue with updated rep. New BLUE will be recomputed below.
                    merged = True
                    break
            if not merged:
                RED.add(qb)
            # Recompute BLUE as children of all RED states
            BLUE.clear()
            for r in RED:
                add_blue_of(r)

        dfa = self._materialize(rep)
        if not self._consistent_with_negatives(dfa):
            # Should not happen; return PTA DFA if so
            return self._materialize(list(range(N)))
        return dfa

def dfa_to_right_linear_grammar(dfa: DFA) -> Tuple[Dict[str, List[List[str]]], str, List[str]]:
    """
    Convert DFA into a right-linear CFG:
      <Qi> -> a <Qj> for each transition i --a--> j
      <Qi> -> [] for each accepting i
    Returns (grammar, start_symbol, alphabet_list)
    """
    g: Dict[str, List[List[str]]] = {}
    def NT(i: int) -> str:
        return f"<Q{i}>"

    n = len(dfa.delta)
    for i in range(n):
        nt = NT(i)
        g.setdefault(nt, [])
        # epsilon for accepting
        if dfa.accept[i]:
            g[nt].append([])
        for a, j in dfa.delta[i].items():
            # Only single-character terminals are expected by our downstream pipeline
            g[nt].append([a, NT(j)])

    start_sym = NT(dfa.start)
    alphabet = sorted(dfa.alphabet) if dfa.alphabet else []
    return g, start_sym, alphabet

def learn_grammar_from_samples(positives: Iterable[str], negatives: Iterable[str]) -> Tuple[Dict[str, List[List[str]]], str, List[str]]:
    """
    Convenience wrapper: learn DFA with RPNI and convert to grammar.
    """
    learner = RPNI(positives, negatives)
    dfa = learner.learn()
    return dfa_to_right_linear_grammar(dfa)
