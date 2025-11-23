#!/usr/bin/env python3
import sys

def main():
    try:
        from lstar.rpni_nfa import rpni_nfa
    except Exception as e:
        print(f"[ERROR] Failed to import rpni_nfa: {e}")
        return 2

    # Toy dataset
    positives = ["a", "b", "ab", "ba"]
    negatives = ["", "aa", "bb", "aba", "bab", "aaa", "bbb"]

    nfa, start = rpni_nfa(positives, negatives)

    pos_ok = all(nfa.accepts(s) for s in positives)
    neg_ok = all(not nfa.accepts(s) for s in negatives)

    print(f"[TEST] Positives accepted: {pos_ok}")
    print(f"[TEST] Negatives rejected: {neg_ok}")
    if not pos_ok:
        bad = [s for s in positives if not nfa.accepts(s)]
        print(f"[FAIL] Positives not accepted: {bad}")
    if not neg_ok:
        bad = [s for s in negatives if nfa.accepts(s)]
        print(f"[FAIL] Negatives accepted: {bad}")
    return 0 if (pos_ok and neg_ok) else 1

if __name__ == "__main__":
    sys.exit(main())
