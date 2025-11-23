"""
Observation table and utilities for Angluin's L* algorithm.

Extracted from notebooks/2024-01-04-lstar-learning-regular-languages.py
and consolidated into a reusable module.
"""

from typing import Dict, List, Tuple
import os


class ObservationTable:
    def __init__(self, alphabet: List[str]):
        # Internal table: row prefix -> {suffix: membership}
        self._T: Dict[str, Dict[str, int]] = {}
        # Prefixes (candidate state access strings), prefix-closed
        self.P: List[str] = [""]
        # Suffixes (distinguishers), suffix-closed
        self.S: List[str] = [""]
        # Alphabet
        self.A: List[str] = alphabet

    # --- Core helpers ---

    def cell(self, v: str, e: str) -> int:
        return self._T[v][e]

    def state(self, p: str) -> str:
        # State identifier is the pattern of 1/0 over S for row p
        return "<%s>" % "".join([str(self.cell(p, s)) for s in self.S])

    # --- Convert observation table to grammar (DFA-as-grammar) ---

    def table_to_grammar(self) -> Tuple[Dict[str, List[List[str]]], str]:
        # Step 1: identify all distinguished states.
        prefix_to_state: Dict[str, str] = {}
        states: Dict[str, List[str]] = {}
        grammar: Dict[str, List[List[str]]] = {}

        for p in self.P:
            stateid = self.state(p)
            if stateid not in states:
                states[stateid] = []
            states[stateid].append(p)
            prefix_to_state[p] = stateid

        for stateid in states:
            grammar[stateid] = []

        # Step 2: start state corresponds to epsilon row
        start_nt = prefix_to_state[""]

        # Step 3: accepting states (rows with T[p, epsilon] == 1)
        accepting = [prefix_to_state[p] for p in self.P if self.cell(p, "") == 1]
        if not accepting:
            # Degenerate grammar with no accepting states
            return {"<start>": []}, "<start>"
        for s in accepting:
            grammar[s] = [["<_>"]]
        grammar["<_>"] = [[]]  # epsilon production

        # Step 4: transitions: [p](a) -> [p.a]
        for sid1 in states:
            first_such_row = states[sid1][0]
            for a in self.A:
                sid2 = self.state(first_such_row + a)
                grammar[sid1].append([a, sid2])

        return grammar, start_nt

    def remove_infinite_loops(
        self, g: Dict[str, List[List[str]]], start: str
    ) -> Tuple[Dict[str, List[List[str]]], str]:
        import math
        import simplefuzzer as fuzzer

        rule_cost = fuzzer.compute_cost(g)
        remove_keys: List[str] = []
        for k in rule_cost:
            if k == start:
                continue
            res = [rule_cost[k][r] for r in rule_cost[k] if rule_cost[k][r] != math.inf]
            if not res:
                remove_keys.append(k)

        cont = True
        while cont:
            cont = False
            new_g: Dict[str, List[List[str]]] = {}
            for k in g:
                if k in remove_keys:
                    continue
                new_g[k] = []
                for r in g[k]:
                    if [t for t in r if t in remove_keys]:
                        continue
                    new_g[k].append(r)
                if not new_g[k]:
                    if k == start:
                        continue
                    remove_keys.append(k)
                    cont = True
            g = new_g
        return g, start

    def grammar(self) -> Tuple[Dict[str, List[List[str]]], str]:
        g, s = self.table_to_grammar()
        return self.remove_infinite_loops(g, s)

    # --- Table maintenance: init/update/closed/consistent and mutators ---

    def init_table(self, oracle) -> None:
        # oracle must provide is_member(string) -> 0/1
        self._T[""] = {"": oracle.is_member("")}
        self.update_table(oracle)

    def update_table(self, oracle) -> None:
        def unique(l: List[str]) -> List[str]:
            return list({s: None for s in l}.keys())

        rows = self.P
        auxrows = [p + a for p in self.P for a in self.A]
        PuPxA = unique(rows + auxrows)
        for p in PuPxA:
            if p not in self._T:
                self._T[p] = {}
            for s in self.S:
                if p in self._T and s in self._T[p]:
                    continue
                self._T[p][s] = oracle.is_member(p + s)

    def closed(self) -> Tuple[bool, str]:
        states_in_P = {self.state(p) for p in self.P}
        P_A = [p + a for p in self.P for a in self.A]
        for t in P_A:
            if self.state(t) not in states_in_P:
                return False, t
        return True, None

    def add_prefix(self, p: str, oracle) -> None:
        if p in self.P:
            return
        self.P.append(p)
        self.update_table(oracle)

    def consistent(self) -> Tuple[bool, Tuple[str, str], str]:
        matchingpairs = [
            (p1, p2)
            for p1 in self.P
            for p2 in self.P
            if p1 != p2 and self.state(p1) == self.state(p2)
        ]
        suffixext = [(a, s) for a in self.A for s in self.S]
        for p1, p2 in matchingpairs:
            for a, s in suffixext:
                if self.cell(p1 + a, s) != self.cell(p2 + a, s):
                    return False, (p1, p2), (a + s)
        return True, None, None

    def add_suffix(self, a_s: str, oracle) -> None:
        if a_s in self.S:
            return
        self.S.append(a_s)
        self.update_table(oracle)
