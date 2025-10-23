#!/usr/bin/env python3
"""
Precompute L* grammar caches in parallel.

- For each (mutation_type, format) pair, read the first K originals/mutated from
  mutated_files/{mutation_type}_{format}.db and build a grammar cache
  cache/lstar_{mutation_type}_{format}.json using:
    python3 lstar-standalone/lstar/repairer_lstar_ec.py --init-cache --grammar-cache ...

- Defaults:
  * K=50
  * Per-job timeout = 1 day (86400s)
  * Penalty pruning env LSTAR_MAX_PENALTY=2 (passed to child)
  * Parallelism: --max-workers (defaults to CPU count)

- You can choose mutation types and formats via CLI; by default tries common sets.

Examples:
  # Build all with K=50, 1-day timeout, penalty=2, 4 workers
  python3 scripts/precompute_caches.py --max-workers 4

  # Only URL/IPv4 for single/double with K=50, penalty=3
  python3 scripts/precompute_caches.py --mutations single double --formats url ipv4 --k 50 --penalty 3
"""
import argparse
import concurrent.futures
import os
import sqlite3
import subprocess
import sys
import time
import signal
from typing import List, Tuple

# Defaults aligned to project
DEFAULT_MUTATIONS = ["single", "double", "triple"]
DEFAULT_FORMATS = ["date", "time", "url", "isbn", "ipv4", "ipv6", "pathfile"]

REPAIRER = ["python3", "-u", "lstar-standalone/lstar/repairer_lstar_ec.py"]

def pick_k_rows(db_path: str, k: int) -> Tuple[List[str], List[str]]:
    """
    Return two lists of strings:
      (orig_list[:k], mutated_list[:k])
    Missing DB or columns produces empty lists.
    """
    if not os.path.exists(db_path):
        print(f"[INFO] Skipping, DB not found: {db_path}")
        return [], []
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT original_text FROM mutations ORDER BY id LIMIT ?", (k,))
        pos = [r[0] or "" for r in cur.fetchall()]
        cur.execute("SELECT mutated_text FROM mutations ORDER BY id LIMIT ?", (k,))
        neg = [r[0] or "" for r in cur.fetchall()]
        conn.close()
        return pos, neg
    except Exception as e:
        print(f"[WARN] Failed to read DB {db_path}: {e}")
        return [], []

def write_list(path: str, lines: List[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for s in lines:
            f.write(s + "\n")

def ensure_dir(path: str) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def build_one(mutation: str, fmt: str, k: int, timeout: int, penalty: int) -> Tuple[str, bool, float, str]:
    """
    Build cache for one (mutation, format).
    Returns: (key, success, elapsed_sec, extra_info)
    """
    key = f"{mutation}_{fmt}"
    db_path = os.path.join("mutated_files", f"{key}.db")
    # Write shared cache per format so bm_single/bm_multiple/bm_triple can all reuse it
    cache_path = os.path.join("cache", f"lstar_{fmt}.json")
    ensure_dir(cache_path)

    pos, neg = pick_k_rows(db_path, k)
    if not pos and not neg:
        return key, False, 0.0, "no-samples-or-db-missing"

    pos_file = f"temp_pos_cache_{key}_{os.getpid()}_{int(time.time()*1000)%100000}.txt"
    neg_file = f"temp_neg_cache_{key}_{os.getpid()}_{int(time.time()*1000)%100000}.txt"

    start = time.time()
    try:
        write_list(pos_file, pos)
        write_list(neg_file, neg)
        category = {
            "date": "Date",
            "time": "Time",
            "url": "URL",
            "isbn": "ISBN",
            "ipv4": "IPv4",
            "ipv6": "IPv6",
            "pathfile": "FilePath",
        }.get(fmt, fmt)

        cmd = [
            *REPAIRER,
            "--positives", pos_file,
            "--negatives", neg_file,
            "--category", category,
            "--grammar-cache", cache_path,
            "--init-cache",
            "--unknown-policy", os.environ.get("LSTAR_UNKNOWN_POLICY", "negative"),
        ]
        env = dict(os.environ)
        env.setdefault("LSTAR_MAX_PENALTY", str(penalty))
        env.setdefault("PYTHONUNBUFFERED", "1")
        print(f"[DEBUG] Precompute {key}: {' '.join(cmd)} (K={k}, timeout={timeout}s, penalty={penalty})")
        # Stream logs to disk while running so we capture output even on timeout/kill
        logs_dir = os.path.join("logs", "precompute")
        os.makedirs(logs_dir, exist_ok=True)
        out_path = os.path.join(logs_dir, f"{key}.out")
        err_path = os.path.join(logs_dir, f"{key}.err")
        with open(out_path, "w", encoding="utf-8") as fo, open(err_path, "w", encoding="utf-8") as fe:
            proc = subprocess.Popen(cmd, stdout=fo, stderr=fe, text=True, env=env)
            rc = None
            try:
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Graceful terminate then force kill
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    rc = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    # Mark as killed by signal if possible
                    rc = -signal.SIGKILL
        elapsed = time.time() - start

        size = os.path.getsize(cache_path) if os.path.exists(cache_path) else 0
        if rc is not None and rc < 0:
            sig = -rc
            try:
                sig_name = signal.Signals(sig).name
            except Exception:
                sig_name = f"SIG{sig}"
            print(f"[INFO] Precompute {key} finished in {elapsed:.2f}s, cache_size={size} bytes, returncode={rc} ({sig_name})")
        else:
            print(f"[INFO] Precompute {key} finished in {elapsed:.2f}s, cache_size={size} bytes, returncode={rc}")

        if rc != 0:
            # Reference full logs (first 400 chars preview printed for convenience)
            print(f"[WARN] Non-zero return for {key}. log_out={out_path}, log_err={err_path}")
            try:
                with open(out_path, "r", encoding="utf-8") as fo:
                    out_preview = fo.read(400)
                with open(err_path, "r", encoding="utf-8") as fe:
                    err_preview = fe.read(400)
            except Exception:
                out_preview, err_preview = "", ""
            print(f"[WARN] stdout[:400]:\n{out_preview}")
            print(f"[WARN] stderr[:400]:\n{err_preview}")

        return key, rc == 0 and os.path.exists(cache_path), elapsed, f"size={size}"
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        print(f"[WARN] Precompute timeout for {key} after {elapsed:.2f}s (limit={timeout}s)")
        return key, False, elapsed, "timeout"
    except Exception as e:
        elapsed = time.time() - start
        print(f"[WARN] Precompute failed for {key} after {elapsed:.2f}s: {e}")
        return key, False, elapsed, f"error={e}"
    finally:
        for p in (pos_file, neg_file):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

def main():
    ap = argparse.ArgumentParser(description="Precompute L* grammar caches (parallel).")
    ap.add_argument("--mutations", nargs="+", default=DEFAULT_MUTATIONS, help=f"Mutation types to include (default: {DEFAULT_MUTATIONS})")
    ap.add_argument("--formats", nargs="+", default=DEFAULT_FORMATS, help=f"Formats to include (default: {DEFAULT_FORMATS})")
    ap.add_argument("--k", type=int, default=100, help="Number of original/mutated samples to use for learning (default: 50)")
    ap.add_argument("--timeout", type=int, default=86400, help="Per-job timeout in seconds (default: 86400, ~1 day)")
    ap.add_argument("--penalty", type=int, default=int(os.environ.get("LSTAR_PRECOMP_MAX_PENALTY", "2")), help="Penalty pruning for child repairer (default from env LSTAR_PRECOMP_MAX_PENALTY or 2)")
    ap.add_argument("--max-workers", type=int, default=(os.cpu_count() or 4), help="Parallel workers (default: cpu count)")
    args = ap.parse_args()

    # Build per-format caches using single_* mutated DBs regardless of mutation type
    formats = args.formats
    pairs = [("single", f) for f in formats]
    print(f"[INFO] Precompute start: formats={len(formats)} (source=single_*), k={args.k}, timeout={args.timeout}s, penalty={args.penalty}, workers={args.max_workers}")

    results = []
    start_all = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        fut_to_key = {
            ex.submit(build_one, m, f, args.k, args.timeout, args.penalty): (m, f)
            for (m, f) in pairs
        }
        for fut in concurrent.futures.as_completed(fut_to_key):
            m, f = fut_to_key[fut]
            try:
                key, ok, elapsed, extra = fut.result()
                results.append((key, ok, elapsed, extra))
            except Exception as e:
                results.append((f"{m}_{f}", False, 0.0, f"worker-error={e}"))

    total_elapsed = time.time() - start_all
    ok_cnt = sum(1 for _, ok, _, _ in results if ok)
    print(f"[SUMMARY] Precompute done in {total_elapsed:.2f}s: success={ok_cnt}/{len(results)}")
    for key, ok, elapsed, extra in sorted(results):
        print(f"[SUMMARY] {key}: {'OK' if ok else 'FAIL'} in {elapsed:.2f}s ({extra})")

if __name__ == "__main__":
    sys.exit(main())
