"""
Positive-only regular grammar inference utilities.

When only positive examples are available (no membership oracle, no negatives),
there is no unique target language identifiable in general. A safe and useful
baseline is to infer the exact language that consists of the given positive
samples — i.e., construct the Prefix Tree Acceptor (PTA) and emit its equivalent
right-linear grammar. This accepts exactly the provided examples and nothing else.

API:
- infer_exact_from_positives(positives: list[str]) -> (grammar: dict, start_nt: str)
"""

from typing import Dict, List, Tuple


def infer_exact_from_positives(positives: List[str]) -> Tuple[Dict[str, List[List[str]]], str]:
    """
    Build a trie (Prefix Tree Acceptor, PTA) from the positive samples and
    convert it into a right-linear grammar that accepts exactly those samples.

    Grammar format (compatible with this repo's fuzzing tooling):
      - dict[str, list[list[str]]] where each nonterminal maps to a list of productions
      - epsilon is represented by an empty list [] (we add a dedicated nonterminal "<_>" -> [])
      - start symbol returned separately

    Example:
        g, s = infer_exact_from_positives(["", "ab", "abc", "b"])
    """
    # 1) Build PTA as a deterministic automaton over implicit alphabet
    # Represent states as integer node IDs; transitions[node][symbol] = next_node
    transitions: Dict[int, Dict[str, int]] = {}
    accepting: set[int] = set()
    next_id = 0

    def new_state() -> int:
        nonlocal next_id
        sid = next_id
        next_id += 1
        transitions[sid] = {}
        return sid

    start_state = new_state()

    # Insert each positive string into the trie; mark terminal state accepting
    for s in positives:
        state = start_state
        for ch in s:
            ds = transitions[state]
            if ch not in ds:
                ds[ch] = new_state()
            state = ds[ch]
        accepting.add(state)

    # 2) Convert PTA to right-linear grammar:
    # For each state q, create a nonterminal "<qN>".
    # If q is accepting, add ["<_>"] (epsilon via "<_>": []),
    # and for each transition q --a--> r, add [a, "<qR>"].
    grammar: Dict[str, List[List[str]]] = {}
    state_to_nt: Dict[int, str] = {q: f"<q{q}>" for q in transitions.keys()}

    # epsilon nonterminal
    grammar["<_>"] = [[]]

    for q, out in transitions.items():
        nt = state_to_nt[q]
        prods: List[List[str]] = []
        if q in accepting:
            prods.append(["<_>"])
        for a, r in out.items():
            prods.append([a, state_to_nt[r]])
        grammar[nt] = prods

    start_nt = state_to_nt[start_state]
    return grammar, start_nt
