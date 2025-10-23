"""
Teacher and Oracle implementations for Angluin's L* algorithm with PAC learning.

Extracted from notebooks/2024-01-04-lstar-learning-regular-languages.py
and consolidated into a reusable module.
"""

from typing import Dict, List, Tuple, Optional
import math
import random

import simplefuzzer as fuzzer
import rxfuzzer
import earleyparser
import cfgrandomsample
import cfgremoveepsilon


class Oracle:
    def is_member(self, q: str) -> int:
        raise NotImplementedError


class Teacher(Oracle):
    """
    PAC-based teacher using a regular expression as the target language.

    - is_member(q): membership query
    - is_equivalent(grammar, start): PAC-style equivalence check that
      tries to find short counterexamples first (cooperative teacher).
    """

    def __init__(self, rex: str, delta: float = 0.1, epsilon: float = 0.1):
        self.g, self.s = rxfuzzer.RegexToGrammar().to_grammar(rex)
        self.parser = earleyparser.EarleyParser(self.g)
        self.sampler = cfgrandomsample.RandomSampleCFG(self.g)
        self.equivalence_query_counter = 0
        self.delta, self.epsilon = delta, epsilon

    # Membership query
    def is_member(self, q: str) -> int:
        try:
            list(self.parser.recognize_on(q, self.s))
        except Exception:
            return 0
        return 1

    # PAC-style equivalence query
    def is_equivalent(
        self, grammar: Dict[str, List[List[str]]], start: str, max_length_limit: int = 10
    ) -> Tuple[bool, Optional[str]]:
        """
        Returns (True, None) if equivalent up to the PAC bound, else (False, counterexample).
        Tries increasing lengths up to max_length_limit, aiming for short counterexamples.
        """
        self.equivalence_query_counter += 1
        num_calls = math.ceil(
            1.0 / self.epsilon
            * (math.log(1.0 / self.delta + self.equivalence_query_counter * math.log(2)))
        )

        for limit in range(1, max_length_limit):
            is_eq, counterex, _ = self.is_equivalent_for(
                self.g, self.s, grammar, start, limit, num_calls
            )
            if counterex is None:  # no members at this length
                continue
            if not is_eq:
                c = [a for a in counterex if a is not None][0]
                return False, c
        return True, None

    # Due to limitations of random sampling, remove epsilon tokens except at start
    def fix_epsilon(
        self, grammar_: Dict[str, List[List[str]]], start: str
    ) -> Tuple[Dict[str, List[List[str]]], str]:
        # deep-ish clone
        grammar = {k: [[t for t in r] for r in grammar_[k]] for k in grammar_}
        try:
            gs = cfgremoveepsilon.GrammarShrinker(grammar, start)
            gs.remove_epsilon_rules()
            return gs.grammar, start
        except Exception:
            # Fallback: inline "<_>" -> [] when used as the sole symbol in a production,
            # and keep "<_>" -> [] available if referenced.
            for k, prods in list(grammar.items()):
                new_prods = []
                for r in prods:
                    if len(r) == 1 and r[0] == "<_>":
                        new_prods.append([])  # convert to direct epsilon production
                    else:
                        new_prods.append(r)
                grammar[k] = new_prods
            if "<_>" not in grammar:
                grammar["<_>"] = [[]]
            return grammar, start

    # Helpers for sampling and parsing comparison
    def digest_grammar(
        self, g: Dict[str, List[List[str]]], s: str, l: int, n: int
    ) -> Tuple[int, Optional[object], Optional[earleyparser.EarleyParser]]:
        if not g.get(s):
            return 0, None, None
        g, s = self.fix_epsilon(g, s)
        rgf = cfgrandomsample.RandomSampleCFG(g)
        key_node = rgf.key_get_def(s, l)
        cnt = key_node.count
        ep = earleyparser.EarleyParser(g)
        return cnt, key_node, ep

    def gen_random(self, key_node, cnt: int) -> Optional[str]:
        if cnt == 0:
            return None
        at = random.randint(0, cnt - 1)
        # sampler does not store state; decode string from key_node
        st_ = self.sampler.key_get_string_at(key_node, at)
        return fuzzer.tree_to_string(st_)

    # Check two grammars for equivalence at a given length and sample count
    def is_equivalent_for(
        self,
        g1: Dict[str, List[List[str]]],
        s1: str,
        g2: Dict[str, List[List[str]]],
        s2: str,
        l: int,
        n: int,
    ) -> Tuple[bool, Optional[Tuple[Optional[str], Optional[str]]], int]:
        cnt1, key_node1, ep1 = self.digest_grammar(g1, s1, l, n)
        cnt2, key_node2, ep2 = self.digest_grammar(g2, s2, l, n)
        count = 0

        str1 = {self.gen_random(key_node1, cnt1) for _ in range(n)}
        str2 = {self.gen_random(key_node2, cnt2) for _ in range(n)}

        for st1 in str1:
            if st1 is None:
                continue
            count += 1
            try:
                list(ep2.recognize_on(st1, s2))
            except Exception:
                return False, (st1, None), count

        for st2 in str2:
            if st2 is None:
                continue
            count += 1
            try:
                list(ep1.recognize_on(st2, s1))
            except Exception:
                return False, (None, st2), count

        return True, None, count
