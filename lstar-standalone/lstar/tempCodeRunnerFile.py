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
