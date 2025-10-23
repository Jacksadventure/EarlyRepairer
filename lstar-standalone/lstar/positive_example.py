"""
Demo: Infer a regular grammar from positive examples only (PTA exact language).

Usage:
  python lstar/positive_example.py                # uses built-in toy samples
  python lstar/positive_example.py path/to/positives.txt
    - File format: one string per line; empty line means epsilon (empty string).
"""

import os, sys, glob
from typing import List

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

from lstar.positive_inference import infer_exact_from_positives
import simplefuzzer as fuzzer


def read_positives_from_file(path: str) -> List[str]:
    positives: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            # Treat an empty line as epsilon
            if line.endswith("\n"):
                line = line[:-1]
            positives.append(line)
    return positives


def main():
    if len(sys.argv) > 1:
        positives = read_positives_from_file(sys.argv[1])
    else:
        positives = ["", "ab", "abc", "b"]

    # Deduplicate while preserving order
    seen = set()
    uniq: List[str] = []
    for s in positives:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    positives = uniq

    print("Positives ({}):".format(len(positives)))
    for s in positives:
        print("  -", repr(s))

    g, s = infer_exact_from_positives(positives)
    print("\nStart symbol:", s)
    print("Nonterminals:", sorted(g.keys()))
    print("\nGrammar rules:")
    for nt, prods in g.items():
        for r in prods:
            print(" ", nt, "->", " ".join(r) if r else "ε")

    # Generate a few samples (should be exactly from the positive set)
    gf = fuzzer.LimitFuzzer(g)
    print("\nSamples from inferred grammar:")
    for _ in range(min(5, max(1, len(positives)))):
        print("-", gf.iter_fuzz(key=s, max_depth=100))


if __name__ == "__main__":
    main()
