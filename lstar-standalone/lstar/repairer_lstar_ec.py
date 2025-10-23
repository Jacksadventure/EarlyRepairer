#!/usr/bin/env python3
"""
Repairer: RPNI DFA inference + Error-Correcting Earley

Pipeline:
1) Learn a DFA via RPNI from positive/negative samples, then convert to a right-linear CFG.
2) Build a covering grammar and use error-correcting Earley to repair broken inputs.
3) Validate repaired outputs with an oracle (e.g., python3 match.py Date).
4) If oracle fails, incrementally add the failing negative example to Teacher.negatives and relearn (<= max_attempts).

Usage example:
  python3 lstar-standalone/lstar/repairer_lstar_ec.py \
    --positives positive/positives.txt \
    --negatives negative/negatives.txt \
    --category Date \
    --limit 10 \
    --max-attempts 5

Notes:
- Does NOT depend on simplefuzzer.
- Requires earleyparser (vendored wheel in lstar-standalone/py) and sympy (installed).
- No membership oracle is used; RPNI learns from provided positive/negative samples.
"""

import os
import sys
import glob
import argparse
import subprocess
import tempfile
import traceback
import json
import time
from typing import List, Set, Tuple, Dict, Any, Optional

# Ensure project root (lstar-standalone) on sys.path so 'lstar' and vendored wheels work
ROOT_DIR = os.path.dirname(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Ensure local vendored wheels in py/ are importable (earleyparser, etc.)
PY_DIR = os.path.join(ROOT_DIR, "py")
if os.path.isdir(PY_DIR):
    if PY_DIR not in sys.path:
        sys.path.insert(0, PY_DIR)
    for whl in glob.glob(os.path.join(PY_DIR, "*.whl")):
        if whl not in sys.path:
            sys.path.append(whl)

# RPNI import (passive DFA learning from samples)
from lstar.rpni import learn_grammar_from_samples as rpni_learn_grammar

# Import error-correcting Earley runtime (no side effects)
try:
    from lstar import ec_runtime as ec
except Exception:
    import ec_runtime as ec

# Types
Grammar = Dict[str, List[List[str]]]

def save_grammar_cache(path: str, g: Grammar, start_sym: str, alphabet: List[str]) -> None:
    data = {
        "start_sym": start_sym,
        "alphabet": alphabet,
        "grammar": g,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

def load_grammar_cache(path: str) -> Tuple[Grammar, str, List[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    g = data["grammar"]
    start_sym = data["start_sym"]
    alphabet = data["alphabet"]
    return g, start_sym, alphabet


def read_lines(path: str) -> List[str]:
    vals: List[str] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.endswith("\n"):
                line = line[:-1]
            vals.append(line)
    return vals


def derive_alphabet_from_examples(positives: Set[str], negatives: Set[str]) -> List[str]:
    chars = set()
    for s in list(positives) + list(negatives):
        chars.update(list(s))
    return sorted(chars) if chars else list("ab")


def terminals_of_grammar(g: Grammar) -> List[str]:
    syms = set()
    for nt, alts in g.items():
        for alt in alts:
            for t in alt:
                if not ec.is_nt(t):
                    syms.add(t)
    return sorted(syms)

def expand_set_terminals(g: Grammar, alphabet: List[str]) -> Grammar:
    """
    Expand any set/frozenset terminals in grammar productions into multiple alternatives
    over their member characters, so the downstream Earley implementation only sees
    string terminals. Keeps epsilon (empty) productions untouched.
    """
    import itertools as I
    new_g: Grammar = {}
    for nt, alts in g.items():
        new_alts: List[List[str]] = []
        for alt in alts:
            # alt is a sequence of symbols; expand any set-like terminal
            choices: List[List[str]] = []
            for t in alt:
                if isinstance(t, (set, frozenset)):
                    # expand set-like terminal into alternatives of member strings
                    choices.append([str(x) for x in t])
                else:
                    choices.append([t])
            # Cartesian product across choices; if choices is empty, preserve epsilon
            for prod in I.product(*choices) if choices else [()]:
                new_alts.append(list(prod))
        new_g[nt] = new_alts
    return new_g

def sanitize_grammar(g: Grammar) -> Grammar:
    """
    Ensure all production symbols are strings; convert any residual non-strings
    (e.g., ints, tuples) to strings. Should be run after expand_set_terminals.
    """
    new_g: Grammar = {}
    for nt, alts in g.items():
        nt_str = nt if isinstance(nt, str) else str(nt)
        new_alts: List[List[str]] = []
        for alt in alts:
            new_alt: List[str] = []
            for t in alt:
                if isinstance(t, str):
                    new_alt.append(t)
                else:
                    new_alt.append(str(t))
            new_alts.append(new_alt)
        new_g[nt_str] = new_alts
    return new_g

def assert_no_set_tokens(g: Grammar):
    """
    Raise if any set/frozenset remains anywhere in grammar productions.
    """
    for nt, alts in g.items():
        for alt in alts:
            for t in alt:
                if isinstance(t, (set, frozenset)):
                    raise TypeError(f"Grammar contains set terminal {t} in production {nt} -> {alt}")

def debug_count_symbol_types(g: Grammar):
    """
    Print a brief summary of symbol types in grammar (for diagnostics).
    """
    import collections
    cnt = collections.Counter()
    for nt, alts in g.items():
        for alt in alts:
            for t in alt:
                cnt[type(t).__name__] += 1
    print(f"[DEBUG] Grammar symbol types: {dict(cnt)}")


def learn_grammar(positives: Set[str], negatives: Set[str], unknown_policy: str = "negative") -> Tuple[Grammar, str, List[str]]:
    """
    Learn a right-linear CFG from samples using RPNI (no membership oracle).
    unknown_policy is ignored (kept for CLI compatibility).
    """
    t0 = time.time()
    g, start_sym, alphabet = rpni_learn_grammar(positives, negatives)
    t1 = time.time()
    try:
        print(f"[PROFILE] rpni: {t1 - t0:.2f}s, P={len(positives)}, N={len(negatives)}, |A|={len(alphabet)}")
    except Exception:
        print(f"[PROFILE] rpni: {t1 - t0:.2f}s")
    return g, start_sym, alphabet


def earley_correct(g: Grammar, start_sym: str, broken: str, symbols: List[str] = None, log: bool = False, penalty: Optional[int] = None, max_penalty: Optional[int] = None) -> str:
    """
    Use the error-correcting Earley parser with a covering grammar to fix 'broken'.
    If 'penalty' is provided, attempt to select a solution with exactly that correction penalty.
    Falls back to lowest-penalty solution if no parse exists with the requested penalty.
    """
    # If symbols not provided, infer from grammar terminals
    if symbols is None:
        symbols = terminals_of_grammar(g)

    covering_grammar, covering_start = ec.augment_grammar_ex(g, start_sym, symbols=symbols)
    parser = ec.ErrorCorrectingEarleyParser(covering_grammar)
    # Configure parser pruning threshold: CLI-provided > env > default 32
    if max_penalty is None:
        try:
            max_penalty = int(os.getenv("LSTAR_MAX_PENALTY", "32"))
        except Exception:
            max_penalty = 32
    try:
        parser.max_penalty = int(max_penalty)
    except Exception:
        pass
    try:
        se = ec.SimpleExtractorEx(parser, broken, covering_start, penalty=penalty, log=log)
    except Exception as e:
        # If requested penalty is invalid (no parse with that penalty), fall back to minimum-penalty
        if penalty is not None and "Invalid penalty" in str(e):
            if log:
                print(f"[WARN] No solution with penalty={penalty}. Falling back to minimum-penalty solution.")
            se = ec.SimpleExtractorEx(parser, broken, covering_start, penalty=None, log=log)
        else:
            raise
    tree = se.extract_a_tree()
    # Use correction-aware projection that maps covering grammar back to expected terminals
    if hasattr(ec, "tree_to_str_fix_ex"):
        fixed = ec.tree_to_str_fix_ex(tree)
    else:
        fixed = ec.tree_to_str(tree)
    return fixed


def validate_with_match(category: str, text: str) -> bool:
    """
    Validate 'text' using validators/regex/* oracle runners when available, otherwise fallback to match.py.
    Returns True on success (exit code 0).
    """
    # Write to temp file
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as tf:
        tf.write(text)
        temp_path = tf.name
    try:
        # Map category to validator basename
        name_map = {
            "Date": "date",
            "Time": "time",
            "URL": "url",
            "ISBN": "isbn",
            "IPv4": "ipv4",
            "IPv6": "ipv6",
            "FilePath": "pathfile",
        }
        base = name_map.get(category, category.lower())

        candidates = [
            os.path.join("validators", "regex", f"validate_{base}"),
            os.path.join("validators", f"validate_{base}"),
        ]
        cmd = None
        for c in candidates:
            if os.path.exists(c):
                cmd = [c, temp_path]
                break
        if cmd is None:
            # Fallback to Python validator
            cmd = ["python3", "match.py", category, temp_path]

        print(f"[DEBUG] Oracle cmd: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return res.returncode == 0
    finally:
        try:
            os.remove(temp_path)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="Repair erroneous inputs using RPNI-inferred DFA grammar + Error-Correcting Earley")
    ap.add_argument("--positives", help="Path to positives.txt (one string per line; empty line is epsilon). Optional if --grammar-cache exists.")
    ap.add_argument("--negatives", help="Path to negatives.txt (initial negative set; optional)")
    ap.add_argument("--broken-file", help="Path to file with broken inputs (one per line; optional)")
    ap.add_argument("--broken", help="Single broken input string to repair (optional)")
    ap.add_argument("--output-file", help="If given and exactly one broken input is processed, write repaired text here")
    ap.add_argument("--grammar-cache", help="Path to cache JSON for learned grammar. If exists (and no --init-cache), it will be loaded; else a new cache will be saved after learning.")
    ap.add_argument("--init-cache", action="store_true", help="Force re-learn from provided pos/neg and overwrite the cache at --grammar-cache.")
    ap.add_argument("--category", required=True, choices=["Date","Time","URL","ISBN","IPv4","IPv6","FilePath"], help="Oracle category for match.py")
    ap.add_argument("--max-attempts", type=int, default=5, help="Max attempts to relearn with added negatives on oracle failure")
    ap.add_argument("--limit", type=int, default=10, help="Limit number of negatives to process (for quick runs)")
    ap.add_argument("--unknown-policy", default="negative", choices=["negative","positive","error"], help="Unknown membership policy for SampleTeacher")
    ap.add_argument("--log", action="store_true", help="Verbose logs for ErrorCorrectingEarley")
    ap.add_argument("--penalty", type=int, help="Target correction penalty to select (capped at 8). Omit to choose minimum-penalty solution.")
    ap.add_argument("--max-penalty", type=int, default=32, help="Max correction penalty allowed during parsing (higher tolerates longer junk). Overrides env LSTAR_MAX_PENALTY.")
    ap.add_argument("--update-cache-on-relearn", action="store_true", help="If set, overwrite the grammar cache on relearning attempts. Default keeps the original cache intact.")
    args = ap.parse_args()

    # Normalize/cap penalty
    penalty_val = None
    if getattr(args, "penalty", None) is not None:
        p = max(0, int(args.penalty))
        if p > 8:
            if args.log:
                print(f"[WARN] --penalty {p} exceeds max of 8; capping to 8.")
            p = 8
        penalty_val = p

    pos_lines = read_lines(args.positives) if args.positives and os.path.isfile(args.positives) else []
    neg_lines = read_lines(args.negatives) if args.negatives and os.path.isfile(args.negatives) else []
    broken_inputs: List[str] = []
    if getattr(args, "broken_file", None) and os.path.isfile(args.broken_file):
        broken_inputs += read_lines(args.broken_file)
    if getattr(args, "broken", None):
        broken_inputs.append(args.broken)
    # de-dup preserving order
    _seen = set()
    broken_inputs = [x for x in broken_inputs if not (x in _seen or _seen.add(x))]

    positives = set(pos_lines)

    # Initialize negatives set from provided negatives file (initial hypothesis)
    teacher_negatives: Set[str] = set(neg_lines)

    print(f"[INFO] Loaded positives={len(positives)}, negatives={len(teacher_negatives)}, broken_inputs={len(broken_inputs)}")
    # Handle grammar cache: load if available (and not init), otherwise learn and optionally save
    g: Grammar
    start_sym: str
    alphabet: List[str]
    cache_path = args.grammar_cache

    if cache_path and os.path.exists(cache_path) and not args.init_cache:
        print(f"[INFO] Loading grammar cache from {cache_path}")
        g, start_sym, alphabet = load_grammar_cache(cache_path)
        # Basic sanity: ensure strings-only grammar
        assert_no_set_tokens(g)
        try:
            print(f"[INFO] Cache stats: nonterminals={len(g)}, productions={sum(len(v) for v in g.values())}, alphabet={len(alphabet)}, size={os.path.getsize(cache_path)} bytes")
        except Exception:
            pass
    else:
        if not positives and cache_path and os.path.exists(cache_path) and args.init_cache:
            print("[ERROR] --init-cache specified but no positives provided to relearn.")
            return
        if not positives and not cache_path:
            print("[ERROR] No positives provided and no grammar cache to load.")
            return
        print(f"[INFO] Learning initial grammar with provided samples ...")
        t_learn0 = time.time()
        g_raw, start_sym, alphabet = learn_grammar(positives, teacher_negatives, unknown_policy=args.unknown_policy)
        t_learn1 = time.time()
        t_prep0 = time.time()
        # Sanitize to make it JSON-serializable and friendly for the parser
        g = sanitize_grammar(expand_set_terminals(g_raw, alphabet))
        assert_no_set_tokens(g)
        t_prep1 = time.time()
        print(f"[PROFILE] learn_grammar(total): {t_learn1 - t_learn0:.2f}s; sanitize+expand: {t_prep1 - t_prep0:.2f}s")
        print(f"[INFO] Learned start symbol: {start_sym}; Nonterminals: {len(g)}; Alphabet(chars): {len(alphabet)}")
        if cache_path:
            try:
                save_grammar_cache(cache_path, g, start_sym, alphabet)
                print(f"[INFO] Saved grammar cache to {cache_path}")
            except Exception as e:
                print(f"[WARN] Failed to save grammar cache to {cache_path}: {e}")

    if not broken_inputs:
        print("[INFO] No broken inputs provided. Exiting after grammar learning/caching.")
        return

    processed = 0
    successes = 0
    failures = 0

    for broken in broken_inputs:
        if args.limit is not None and processed >= args.limit:
            break
        processed += 1
        print(f"\n[CASE {processed}] Broken: {repr(broken)}")

        # Attempt repair with current grammar
        try:
            # Normalize grammar only if needed; profile time
            t0 = time.time()
            if any(isinstance(t, (set, frozenset)) for alts in g.values() for alt in alts for t in alt):
                g_norm = sanitize_grammar(expand_set_terminals(g, alphabet))
            else:
                g_norm = g
            assert_no_set_tokens(g_norm)
            t1 = time.time()
            print(f"[PROFILE] normalize: {t1 - t0:.2f}s")

            debug_count_symbol_types(g_norm)

            t2 = time.time()
            fixed = earley_correct(g_norm, start_sym, broken, symbols=alphabet, log=args.log, penalty=penalty_val, max_penalty=args.max_penalty)
            t3 = time.time()
            print(f"[PROFILE] ec_earley: {t3 - t2:.2f}s")

            t4 = time.time()
            ok = validate_with_match(args.category, fixed)
            t5 = time.time()
            print(f"[PROFILE] oracle_validate: {t5 - t4:.2f}s")

            print(f"[ATTEMPT 0] Fixed: {repr(fixed)} | Oracle: {'OK' if ok else 'FAIL'}")
            # If an output file is requested (bm_xxx integration), write the repaired text
            if getattr(args, "output_file", None):
                try:
                    with open(args.output_file, "w", encoding="utf-8") as outf:
                        outf.write(fixed)
                except Exception:
                    pass
            if ok:
                successes += 1
                continue
        except Exception as e:
            print(f"[ATTEMPT 0] Error during correction: {e}")
            print(traceback.format_exc())
            ok = False

        # If oracle failed, add this broken example to Teacher.negatives and relearn up to max-attempts
        attempt = 1
        cur_ok = ok
        while attempt <= args.max_attempts and not cur_ok:
            teacher_negatives.add(broken)
            print(f"[INFO] Re-learning with {len(teacher_negatives)} negative(s) (attempt {attempt}/{args.max_attempts}) ...")
            try:
                t_learn0 = time.time()
                g_raw, start_sym, alphabet = learn_grammar(positives, teacher_negatives, unknown_policy=args.unknown_policy)
                t_learn1 = time.time()
                t_prep0 = time.time()
                g = sanitize_grammar(expand_set_terminals(g_raw, alphabet))
                t_prep1 = time.time()
                print(f"[PROFILE] learn_grammar(relearn): {t_learn1 - t_learn0:.2f}s; sanitize+expand(relearn): {t_prep1 - t_prep0:.2f}s")
                # If cache provided, refresh it when relearning only when explicitly requested
                if cache_path and getattr(args, "update_cache_on_relearn", False):
                    try:
                        save_grammar_cache(cache_path, g, start_sym, alphabet)
                        print(f"[INFO] Refreshed grammar cache at {cache_path}")
                    except Exception as e:
                        print(f"[WARN] Failed to refresh grammar cache at {cache_path}: {e}")
            except Exception as e:
                print(f"[ERROR] RPNI learning failed on attempt {attempt}: {e}")
                break

            try:
                t0 = time.time()
                if any(isinstance(t, (set, frozenset)) for alts in g.values() for alt in alts for t in alt):
                    g_norm = sanitize_grammar(expand_set_terminals(g, alphabet))
                else:
                    g_norm = g
                assert_no_set_tokens(g_norm)
                t1 = time.time()
                print(f"[PROFILE] normalize(relearn): {t1 - t0:.2f}s")

                debug_count_symbol_types(g_norm)

                t2 = time.time()
                fixed = earley_correct(g_norm, start_sym, broken, symbols=alphabet, log=args.log, penalty=penalty_val, max_penalty=args.max_penalty)
                t3 = time.time()
                print(f"[PROFILE] ec_earley(relearn): {t3 - t2:.2f}s")

                t4 = time.time()
                cur_ok = validate_with_match(args.category, fixed)
                t5 = time.time()
                print(f"[PROFILE] oracle_validate(relearn): {t5 - t4:.2f}s")

                print(f"[ATTEMPT {attempt}] Fixed: {repr(fixed)} | Oracle: {'OK' if cur_ok else 'FAIL'}")
                # Update output file with the latest repaired text if requested
                if getattr(args, "output_file", None):
                    try:
                        with open(args.output_file, "w", encoding="utf-8") as outf:
                            outf.write(fixed)
                    except Exception:
                        pass
            except Exception as e:
                print(f"[ATTEMPT {attempt}] Error during correction: {e}")
                print(traceback.format_exc())
                cur_ok = False

            attempt += 1

        if cur_ok:
            successes += 1
        else:
            failures += 1

    print(f"\n[SUMMARY] Processed={processed}, Successes={successes}, Failures={failures}")


if __name__ == "__main__":
    main()
