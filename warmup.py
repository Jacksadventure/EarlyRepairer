#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
warmup.py

Iterative warm-up data collection using RPNI (passive DFA learning) + grammar-based fuzzing.

Flow per round:
 1) Load current labeled samples (positives, negatives).
 2) Learn a right-linear grammar using repo's RPNI implementation.
 3) Fuzz N samples from the learned grammar.
 4) Classify each sample using an oracle (validator command),
    and write them into positive/ and negative/ folders.
 5) Add the newly labeled samples to the training sets and repeat.

Notes:
- RPNI implementation is at: lstar-standalone/lstar/rpni.py
- Oracle can be the regex validators (validators/regex/validate_*) or
  the C++ validators (validators/validate_*), or Python match.py.
- Grammar format saved is compatible with lstar-standalone learners
  (keys: grammar, start_sym, alphabet).

Example usage (Date):
  python3 warmup.py \
    --init-positives positive/positives.txt \
    --rounds 3 \
    --batch-size 1000 \
    --category Date

Or explicitly provide oracle command (must accept a single <file> argument):
  python3 warmup.py --init-positives positive/positives.txt \
    --rounds 2 --batch-size 500 \
    --oracle-cmd validators/regex/validate_date

Outputs:
- Writes labeled files to positive/ and negative/ by default.
- Saves learned grammars to cache/lstar_<category>_round<i>.json and learned_grammar.json.
- Appends all seen samples to cache/warmup_<category>_pos.txt and _neg.txt (dedupbed).
"""

import argparse
import os
import sys
import json
import random
import string
import time
import tempfile
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Iterable, Optional, Set
from importlib.machinery import SourceFileLoader
import simplefuzzer as fuzzer

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CACHE_DIR = os.path.join(REPO_ROOT, "cache")

# --------------
# Utilities
# --------------

def load_rpni_module():
    """Dynamically load lstar-standalone/lstar/rpni.py as a module."""
    rpni_path = os.path.join(REPO_ROOT, "lstar-standalone", "lstar", "rpni.py")
    if not os.path.exists(rpni_path):
        raise FileNotFoundError(f"RPNI not found at {rpni_path}")
    mod = SourceFileLoader("_rpni_dyn", rpni_path).load_module()  # type: ignore[attr-defined]
    return mod


def read_lines(path: Optional[str]) -> List[str]:
    if not path:
        return []
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return [line.rstrip("\n") for line in f]


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


# --------------
# Grammar fuzzing (right-linear grammar) — via simplefuzzer.LimitFuzzer
# --------------


def fuzz_batch(grammar: Dict[str, List[List[str]]], start_sym: str, count: int,
               max_depth: int = 32) -> List[str]:
    """Fuzz `count` samples from a right-linear grammar using LimitFuzzer.

    This uses the same LimitFuzzer-based generation as the rpni_fuzz learner,
    so warmup and the fuzzing RPNI variant are consistent.
    """
    try:
        gf = fuzzer.LimitFuzzer(grammar)
    except Exception:
        return []
    out: List[str] = []
    for _ in range(count):
        try:
            s = gf.iter_fuzz(key=start_sym, max_depth=max_depth)
        except Exception:
            continue
        if isinstance(s, str):
            out.append(s)
    return out


# --------------
# Grammar acceptance (right-linear grammar recognizer for accuracy)
# --------------

def build_dfa_from_right_linear(grammar: Dict[str, List[List[str]]]):
    """Build a DFA-like transition and accepting map from a right-linear grammar
    produced by dfa_to_right_linear_grammar. Returns (trans, accept_map).
    trans: Dict[NT, Dict[char, NT]]
    accept_map: Dict[NT, bool]
    """
    trans: Dict[str, Dict[str, str]] = {}
    accept_map: Dict[str, bool] = {}
    for nt, rules in grammar.items():
        tmap: Dict[str, str] = {}
        is_acc = False
        for r in rules:
            if len(r) == 0:
                is_acc = True
            elif len(r) >= 1:
                a = r[0]
                nxt = r[1] if len(r) > 1 else None
                if nxt is not None:
                    tmap[a] = nxt
        trans[nt] = tmap
        accept_map[nt] = is_acc
    return trans, accept_map


def accepts_right_linear(trans: Dict[str, Dict[str, str]], accept_map: Dict[str, bool],
                          start_sym: str, w: str) -> bool:
    cur = start_sym
    for ch in w:
        nxt = trans.get(cur, {}).get(ch)
        if nxt is None:
            return False
        cur = nxt
    return bool(accept_map.get(cur, False))


# --------------
# Oracle / Validator
# --------------

class Oracle:
    def __init__(self, oracle_cmd: Optional[str], category: Optional[str], timeout: float):
        self.timeout = timeout
        self.oracle_cmd = oracle_cmd
        self.category = category
        # Resolve oracle command if not given explicitly
        if self.oracle_cmd is None:
            if not self.category:
                raise ValueError("Either --oracle-cmd or --category must be provided")
            low = self.category.lower()
            # Prefer regex validator binary if present
            cand = os.path.join(REPO_ROOT, "validators", "regex", f"validate_{low}")
            if os.path.exists(cand):
                self.oracle_cmd = cand
            else:
                # Try earley binary validator, else fallback to Python match.py
                cand2 = os.path.join(REPO_ROOT, "validators", f"validate_{low}")
                if os.path.exists(cand2):
                    self.oracle_cmd = cand2
                else:
                    self.oracle_cmd = f"python3 {os.path.join(REPO_ROOT, 'match.py')} {self.category}"

    def is_valid(self, text: str, tmp_dir: str) -> bool:
        # Write to temp file
        fd, path = tempfile.mkstemp(prefix="warm_", dir=tmp_dir)
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        try:
            # Construct command; supports either single binary or "python3 match.py Category"
            if "{file}" in self.oracle_cmd:
                cmd = self.oracle_cmd.format(file=path)
                shell = True
            else:
                parts = self.oracle_cmd.split()
                cmd = parts + [path]
                shell = False
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout, shell=shell)
            return res.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            return False
        finally:
            try:
                os.remove(path)
            except Exception:
                pass


# --------------
# Main iterative loop
# --------------

def run_rounds(init_pos: List[str], init_neg: List[str],
               oracle: Oracle, out_pos_dir: str, out_neg_dir: str,
               rounds: int, batch_size: int, save_tag: str,
               dedup_cache_dir: str, max_steps: int) -> None:
    rpni = load_rpni_module()

    # Dedup across rounds
    pos_set: Set[str] = set([s for s in init_pos if s is not None])
    neg_set: Set[str] = set([s for s in init_neg if s is not None])

    os.makedirs(out_pos_dir, exist_ok=True)
    os.makedirs(out_neg_dir, exist_ok=True)
    os.makedirs(dedup_cache_dir, exist_ok=True)
    tmp_dir = os.path.join(REPO_ROOT, "tmp_warmup")
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    os.makedirs(tmp_dir, exist_ok=True)

    # Resume support: merge any previously accumulated cache files
    cache_pos_path = os.path.join(dedup_cache_dir, f"warmup_{save_tag}_pos.txt")
    cache_neg_path = os.path.join(dedup_cache_dir, f"warmup_{save_tag}_neg.txt")
    pos_set.update(read_lines(cache_pos_path))
    neg_set.update(read_lines(cache_neg_path))

    for r in range(1, rounds + 1):
        t0 = time.time()
        # 1) Learn grammar from current samples
        g, start_sym, alphabet = rpni.learn_grammar_from_samples(pos_set, neg_set)
        # Persist grammar for this round and as last-learned
        round_cache = os.path.join(DEFAULT_CACHE_DIR, f"lstar_{save_tag}_round{r}.json")
        learned = {"grammar": g, "start_sym": start_sym, "alphabet": alphabet}
        write_json(round_cache, learned)
        write_json(os.path.join(REPO_ROOT, "learned_grammar.json"), learned)

        # Build DFA view for accuracy computation
        trans, accept_map = build_dfa_from_right_linear(g)

        # 2) Fuzz batch from grammar using LimitFuzzer (same as rpni_fuzz learner)
        samples = fuzz_batch(g, start_sym, batch_size, max_depth=max_steps)

        # 3) Classify with oracle (parallel)
        added_neg = 0
        to_write_neg: List[str] = []
        # Accuracy counters
        total = len(samples)
        correct = 0
        nworkers = min(32, max(4, os.cpu_count() or 4))
        with ThreadPoolExecutor(max_workers=nworkers) as ex:
            futs = {ex.submit(oracle.is_valid, s, tmp_dir): s for s in samples}
            for fut in as_completed(futs):
                s = futs[fut]
                try:
                    ok = fut.result()
                except Exception:
                    ok = False
                # Predicted by learned grammar (DFA view)
                try:
                    pred_ok = accepts_right_linear(trans, accept_map, start_sym, s)
                except Exception:
                    pred_ok = False
                if pred_ok == ok:
                    correct += 1
                # Only add new *negative* examples to the training sets
                if not ok and s not in neg_set:
                    neg_set.add(s)
                    to_write_neg.append(s)
                    added_neg += 1

        # 4) Persist newly labeled negatives to folders and caches
        for idx, s in enumerate(to_write_neg, start=1):
            fname = os.path.join(out_neg_dir, f"warm_neg_r{r}_{idx:05d}.txt")
            try:
                with open(fname, "w", encoding="utf-8") as f:
                    f.write(s)
            except Exception:
                pass
        # Append negatives to cache list
        if added_neg:
            with open(cache_neg_path, "a", encoding="utf-8") as f:
                for s in to_write_neg:
                    f.write(s + "\n")

        dt = time.time() - t0
        acc = (correct / total) if total else 0.0
        print(f"[ROUND {r}] learned states: {len(g)} | batch={batch_size} | +neg={added_neg} | acc={acc:.3f} | time={dt:.2f}s")

    # Cleanup tmp
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


# --------------
# CLI
# --------------

def main():
    ap = argparse.ArgumentParser(description="Warm-up iterative grammar learning with RPNI + grammar fuzzer")
    ap.add_argument("--init-positives", required=True, help="Path to initial positives.txt (one per line)")
    ap.add_argument("--init-negatives", help="Path to initial negatives.txt (optional)")

    group = ap.add_mutually_exclusive_group(required=False)
    group.add_argument("--oracle-cmd", help="Oracle command; must accept a file argument. Example: 'validators/regex/validate_date' or 'python3 match.py Date' or template with {file}")
    group.add_argument("--category", help="High-level category (Date, Time, URL, ISBN, IPv4, IPv6, FilePath)")

    ap.add_argument("--rounds", type=int, default=3, help="Number of learning rounds (default: 3)")
    ap.add_argument("--batch-size", type=int, default=1000, help="Number of fuzzed samples per round (default: 1000)")
    ap.add_argument("--max-steps", type=int, default=2048, help="Max derivation steps per sample (default: 2048)")
    ap.add_argument("--oracle-timeout", type=float, default=2.0, help="Oracle timeout per sample (seconds)")
    ap.add_argument("--out-positive-dir", default=os.path.join(REPO_ROOT, "positive"), help="Directory to write newly found positives")
    ap.add_argument("--out-negative-dir", default=os.path.join(REPO_ROOT, "negative"), help="Directory to write newly found negatives")
    ap.add_argument("--tag", default=None, help="Tag used in cache and artifact names (default: from category or 'generic')")
    ap.add_argument("--seed", type=int, help="Random seed")

    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    tag = args.tag or (args.category.lower() if args.category else "generic")

    # Load initial datasets
    init_pos = [s for s in read_lines(args.init_positives) if s is not None]
    init_neg = [s for s in read_lines(args.init_negatives) if s is not None]

    if not init_pos:
        print("[ERROR] No initial positives provided. Aborting.")
        sys.exit(2)

    oracle = Oracle(args.oracle_cmd, args.category, args.oracle_timeout)

    try:
        run_rounds(
            init_pos=init_pos,
            init_neg=init_neg,
            oracle=oracle,
            out_pos_dir=args.out_positive_dir,
            out_neg_dir=args.out_negative_dir,
            rounds=args.rounds,
            batch_size=args.batch_size,
            save_tag=tag,
            dedup_cache_dir=DEFAULT_CACHE_DIR,
            max_steps=args.max_steps,
        )
    except KeyboardInterrupt:
        print("[INFO] Interrupted by user.")
    except Exception as e:
        print(f"[ERROR] warmup failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
