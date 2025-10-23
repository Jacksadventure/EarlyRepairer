# L* (Angluin) Learning Utilities

Extracted reusable implementation from `notebooks/2024-01-04-lstar-learning-regular-languages.py`:
- Observation table (P/S sets, closedness, consistency, conversion to grammar)
- PAC-based Teacher/Oracle using regex target language
- L* main loop and a convenience wrapper

## Install runtime deps

This repo already vendored wheels in `py/`. If you run standalone, ensure these Python packages are importable:
- simplefuzzer
- rxfuzzer
- earleyparser
- cfgrandomsample
- cfgremoveepsilon

On this site, imports resolve via the local wheels.

## Quick start

```python
from lstar import ObservationTable, Teacher, l_star, learn_from_regex
import string

# Easiest: learn directly from a regex
g, s = learn_from_regex('(ab|cd|ef)*', alphabet=list(string.ascii_letters))

# Or control the components yourself
teacher = Teacher('(ab|cd|ef)*', delta=0.1, epsilon=0.1)
tbl = ObservationTable(list(string.ascii_letters))
g2, s2 = l_star(tbl, teacher)
assert g == g2 and s == s2
```

## Generate samples from the learned grammar

```python
import simplefuzzer as fuzzer

gf = fuzzer.LimitFuzzer(g)
for _ in range(5):
    print(gf.iter_fuzz(key=s, max_depth=100))
```

## API

- lstar.observation_table.ObservationTable
  - init_table(oracle), update_table(oracle)
  - closed() -> (bool, missing_prefix)
  - consistent() -> (bool, (p1,p2), distinguishing_suffix)
  - add_prefix(p, oracle), add_suffix(s, oracle)
  - grammar() -> (grammar_dict, start_symbol)
- lstar.teacher.Teacher(rex, delta=0.1, epsilon=0.1)
  - is_member(q) -> 0/1
  - is_equivalent(grammar, start) -> (bool, counterexample_or_None)
- lstar.algorithm.l_star(T, teacher)
- lstar.algorithm.learn_from_regex(regex, alphabet, delta=0.1, epsilon=0.1)

## Integrate your own Teacher

To integrate your own teacher with positive and negative inputs, implement an object that provides:
- is_member(q: str) -> 0/1
- is_equivalent(grammar: dict, start: str) -> (bool, counterexample_or_None)

Minimal skeleton:
```python
from typing import Dict, List, Tuple, Optional
from lstar import ObservationTable, l_star
from lstar.teacher import Oracle  # just an interface type

class MyTeacher(Oracle):
    def __init__(self, positives: set[str], negatives: set[str]):
        self.positives = set(positives)
        self.negatives = set(negatives)

    def is_member(self, q: str) -> int:
        # Return 1 if q is in language, else 0
        if q in self.positives: return 1
        if q in self.negatives: return 0
        # Decide what to do for unknowns:
        return 0  # or query your blackbox, or apply a policy

    def is_equivalent(self, grammar: Dict[str, List[List[str]]], start: str) -> Tuple[bool, Optional[str]]:
        # Evaluate the hypothesis grammar against your data or system
        # Option 1: finite-set check — all positives accepted, all negatives rejected
        import earleyparser
        ep = earleyparser.EarleyParser(grammar)
        for p in self.positives:
            try: list(ep.recognize_on(p, start))
            except Exception: return False, p
        for n in self.negatives:
            try:
                list(ep.recognize_on(n, start))
                return False, n
            except Exception:
                pass
        return True, None

# Run L*
positives = {"", "a", "ab", "b"}
negatives = {"aa", "ba", "bb"}
teacher = MyTeacher(positives, negatives)

# Derive alphabet from samples (or specify explicitly)
alphabet = sorted(set("".join(list(positives | negatives)))) or list("ab")

T = ObservationTable(alphabet)
g, s = l_star(T, teacher)
```

You can also use the built-in SampleTeacher if your membership/equivalence are purely based on finite positive/negative sets:
```python
from lstar import ObservationTable, l_star, SampleTeacher

teacher = SampleTeacher(positives={"", "a", "ab", "b"},
                        negatives={"aa", "ba", "bb"},
                        unknown_policy="negative")  # or "positive"/"error"
T = ObservationTable(alphabet=["a","b"])
g, s = l_star(T, teacher)
```

Command-line demo with files:
- python lstar/sample_teacher_example.py positives.txt negatives.txt [unknown_policy]
  - File format: one string per line; empty line means epsilon "".

## Notes

- The grammar format matches the rest of this codebase: dict[str, list[list[str]]]
  with an explicit start symbol; epsilon is represented by an empty list in a production.
- PAC equivalence uses bounded-length cooperative search to return short counterexamples when possible.
