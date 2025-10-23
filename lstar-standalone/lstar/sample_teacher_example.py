"""
Demo: Run L* using a sample-based Teacher with positive and negative examples.

Usage:
  python lstar/sample_teacher_example.py
    - Uses built-in toy positives/negatives

  python lstar/sample_teacher_example.py positives.txt negatives.txt [unknown_policy]
    - positives.txt / negatives.txt: one string per line; empty line means epsilon ""
    - unknown_policy in {"negative","positive","error"}; default "negative"
      * negative: strings not in provided sets are treated as non-members
      * positive: strings not in provided sets are treated as members
      * error:    unknown strings cause membership queries to raise

Alphabet:
  - Derived automatically from all characters present in positives ∪ negatives.
"""

import os, sys, glob
from typing import List, Tuple, Set

# Ensure project root on sys.path so 'lstar' imports work when run as a script
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
# Ensure local vendored wheels in py/ are importable
PY_DIR = os.path.join(ROOT_DIR, "py")
if os.path.isdir(PY_DIR):
    if PY_DIR not in sys.path:
        sys.path.insert(0, PY_DIR)
    for whl in glob.glob(os.path.join(PY_DIR, "*.whl")):
        if whl not in sys.path:
            sys.path.append(whl)

from lstar import ObservationTable, l_star
from lstar.sample_teacher import SampleTeacher
import simplefuzzer as fuzzer


def read_lines(path: str) -> List[str]:
    vals: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.endswith("\n"):
                line = line[:-1]
            # Empty line represents epsilon
            vals.append(line)
    return vals


def derive_alphabet(positives: Set[str], negatives: Set[str]) -> List[str]:
    chars = set()
    for s in list(positives) + list(negatives):
        chars.update(list(s))
    return sorted(chars) if chars else list("ab")


def main():
    # Load data
    if len(sys.argv) >= 3:
        positives = set(read_lines(sys.argv[1]))
        negatives = set(read_lines(sys.argv[2]))
        unknown_policy = sys.argv[3] if len(sys.argv) >= 4 else "negative"
    else:
        # Toy example
        positives = {"", "a", "ab", "b"}
        negatives = {"aa", "ba", "bb"}
        unknown_policy = "negative"

    print("Positives ({}):".format(len(positives)))
    for s in positives:
        print("  +", repr(s))
    print("Negatives ({}):".format(len(negatives)))
    for s in negatives:
        print("  -", repr(s))
    print("unknown_policy:", unknown_policy)

    alphabet = derive_alphabet(positives, negatives)
    print("Alphabet:", alphabet)

    # Build teacher and run L*
    teacher = SampleTeacher(positives=positives, negatives=negatives, unknown_policy=unknown_policy)
    T = ObservationTable(alphabet)
    g, start_sym = l_star(T, teacher)

    print("\nLearned start symbol:", start_sym)
    print("Nonterminals:", sorted(g.keys()))
    print("\nGrammar rules:")
    for nt, prods in g.items():
        for r in prods:
            print(" ", nt, "->", " ".join(r) if r else "ε")

    # Generate a few samples
    gf = fuzzer.LimitFuzzer(g)
    print("\nSamples from learned grammar:")
    for _ in range(5):
        print("-", gf.iter_fuzz(key=start_sym, max_depth=100))

    # Quick evaluation on provided sets
    try:
        import earleyparser
        ep = earleyparser.EarleyParser(g)
        ok_pos = all([_accepts(ep, start_sym, p) for p in positives])
        ok_neg = all([not _accepts(ep, start_sym, n) for n in negatives])
        print("\nChecks on provided sets:")
        print("  All positives accepted:", ok_pos)
        print("  All negatives rejected:", ok_neg)
    except Exception:
        # If earleyparser import fails, we still completed learning
        pass


def _accepts(parser, start, text: str) -> bool:
    try:
        list(parser.recognize_on(text, start))
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
