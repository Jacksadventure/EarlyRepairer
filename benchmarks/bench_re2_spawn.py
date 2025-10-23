#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

def list_files(input_dir: Path):
    return sorted([p for p in input_dir.iterdir() if p.is_file()])

def main():
    ap = argparse.ArgumentParser(description="Benchmark RE2 per-process matching (spawn a new process per match).")
    ap.add_argument("category", choices=["Date", "Time", "URL", "ISBN", "IPv4", "IPv6", "FilePath"], help="Pattern category to use")
    ap.add_argument("input_dir", type=Path, help="Directory containing input files (each file has one sample)")
    ap.add_argument("--iterations", type=int, default=10, help="Number of times to iterate over the dataset (default: 10)")
    ap.add_argument("--binary", type=Path, default=Path("validators/match_re2"), help="Path to the single-file RE2 matcher binary (default: validators/match_re2)")
    ap.add_argument("--warmup", type=int, default=2, help="Warmup runs on the first file before timing (default: 2)")
    args = ap.parse_args()

    if not args.input_dir.exists() or not args.input_dir.is_dir():
        print(f"Input directory not found or not a directory: {args.input_dir}", file=sys.stderr)
        sys.exit(2)

    files = list_files(args.input_dir)
    if not files:
        print(f"No files found in directory: {args.input_dir}", file=sys.stderr)
        sys.exit(2)

    bin_path = args.binary
    if not bin_path.exists():
        print(f"Matcher binary not found: {bin_path}", file=sys.stderr)
        sys.exit(2)

    # Warm-up on the first file to reduce cold start bias
    first_file = files[0]
    for _ in range(max(0, args.warmup)):
        subprocess.run([str(bin_path), args.category, str(first_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    total_checks = len(files) * max(1, args.iterations)
    matches = 0
    reads_ok = len(files) * max(1, args.iterations)  # treat each as a read; the binary reads internally

    t0 = time.perf_counter()
    for _ in range(max(1, args.iterations)):
        for fp in files:
            # Each spawn compiles the regex and matches once
            proc = subprocess.run([str(bin_path), args.category, str(fp)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if proc.returncode == 0:
                matches += 1
    t1 = time.perf_counter()

    elapsed_s = t1 - t0
    elapsed_ms = elapsed_s * 1000.0
    per_check_us = (elapsed_s * 1e6 / total_checks) if total_checks else 0.0
    throughput = (total_checks / elapsed_s) if elapsed_s > 0 else 0.0

    result = {
        "mode": "per-process",
        "category": args.category,
        "files": len(files),
        "iterations": max(1, args.iterations),
        "checks": total_checks,
        "reads_ok": reads_ok,
        "matches": matches,
        "elapsed_ms": elapsed_ms,
        "per_check_us": per_check_us,
        "throughput_checks_per_sec": throughput
    }
    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    main()
