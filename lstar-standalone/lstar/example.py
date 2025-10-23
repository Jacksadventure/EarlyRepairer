"""
Minimal example demonstrating usage of the lstar package.
"""

import os, sys, glob
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

import string
from lstar import learn_from_regex
import simplefuzzer as fuzzer


def main():
    # Learn a grammar equivalent to the regex under PAC assumptions
    g, s = learn_from_regex("(12|cd|ef)*", alphabet=list(string.digits + string.ascii_lowercase), delta=0.1, epsilon=0.1)
    print("Start symbol:", s)
    print("Nonterminals:", sorted(g.keys()))

    # Generate a few samples from the learned grammar
    gf = fuzzer.LimitFuzzer(g)
    print("Samples:")
    for _ in range(5):
        print("-", gf.iter_fuzz(key=s, max_depth=100))


if __name__ == "__main__":
    main()
