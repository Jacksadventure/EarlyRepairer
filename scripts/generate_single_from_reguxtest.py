#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate single_xxx.db for data formats using a reguxtest-like approach:
- For each original file under data/<fmt>/, produce one mutated string that fails match.py validation
- Store into mutated_files/single_<fmt>.db with the same 'mutations' table schema used by mutation_single.py
"""
import os
import sys
import sqlite3
import subprocess
import tempfile
import random
from pathlib import Path
from typing import Tuple, Optional

DIR_TO_CATEGORY = {
    "date": "Date",
    "ipv4": "IPv4",
    "ipv6": "IPv6",
    "isbn": "ISBN",
    "pathfile": "FilePath",
    "time": "Time",
    "url": "URL",
}

REPLACEMENTS = ["*", "#", "%"]  # single-char replacements to try

ROOT = Path(__file__).resolve().parents[1]
MATCH_PY = str(ROOT / "match.py")
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "mutated_files"
OUT_DIR.mkdir(parents=True, exist_ok=True)

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mutations (
               id            INTEGER PRIMARY KEY AUTOINCREMENT,
               file_path     TEXT,
               mutation_pos  INTEGER,
               original_text TEXT,
               mutated_text  TEXT
           );"""
    )
    conn.commit()

def validate_with_matchpy_text(category: str, text: str) -> int:
    """
    Write text to a temp file, run match.py CATEGORY <tmp>, return exit code.
    match.py exit codes:
      0 = full match
     -1 = partial match  (becomes 255 at OS level)
      1 = not matched
    For our purposes, we consider 'invalid' as ret != 0, i.e., not full match.
    """
    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(text)
        tf.flush()
        tmp_path = tf.name
    try:
        res = subprocess.run(
            ["python3", MATCH_PY, category, tmp_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=15,
        )
        return res.returncode
    except subprocess.TimeoutExpired:
        return 1
    except Exception:
        return 1
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

def find_one_invalid_mutation(original: str, fmt: str) -> Optional[Tuple[str, int]]:
    """
    Try to produce a mutated string that fails validation (retcode != 0).
    Strategy:
      1) Single-character replacement at a shuffled index with a char from REPLACEMENTS
      2) Insertion at beginning or end if no replacement works
      3) Deletion of one character if length > 0
    Return (mutated_text, pos) or None if no mutation found (very unlikely).
    """
    category = DIR_TO_CATEGORY.get(fmt)
    if not category:
        return None

    s = original
    n = len(s)

    # try 1-char replacements
    indices = list(range(n))
    random.shuffle(indices)
    for i in indices:
        orig_ch = s[i]
        for rep in REPLACEMENTS:
            if rep == orig_ch:
                continue
            mutated = s[:i] + rep + s[i+1:]
            rc = validate_with_matchpy_text(category, mutated)
            if rc != 0:  # invalid
                return mutated, i

    # try insertion at pos 0 or end
    for pos in (0, n):
        mutated = s[:pos] + REPLACEMENTS[0] + s[pos:]
        rc = validate_with_matchpy_text(category, mutated)
        if rc != 0:
            return mutated, pos

    # try deletion if possible
    if n > 0:
        i = random.randrange(n)
        mutated = s[:i] + s[i+1:]
        rc = validate_with_matchpy_text(category, mutated)
        if rc != 0:
            return mutated, i

    return None

def store_pair(conn: sqlite3.Connection,
               file_path: str,
               pos: int,
               original_text: str,
               mutated_text: str) -> None:
    conn.execute(
        "INSERT INTO mutations (file_path, mutation_pos, original_text, mutated_text) VALUES (?, ?, ?, ?)",
        (file_path, pos, original_text, mutated_text),
    )
    conn.commit()

def process_format(fmt: str) -> None:
    fmt_dir = DATA_DIR / fmt
    if not fmt_dir.is_dir():
        print(f"[SKIP] data/{fmt} not found")
        return

    db_path = OUT_DIR / f"single_{fmt}.db"
    with sqlite3.connect(db_path) as conn:
        ensure_table(conn)
        files = sorted([p for p in fmt_dir.iterdir() if p.is_file()])
        inserted = 0
        for path in files:
            try:
                original = path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                continue
            # Generate one invalid mutation per original (regardless of original validity)
            res = find_one_invalid_mutation(original, fmt)
            if not res:
                continue
            mutated, pos = res
            store_pair(conn, str(path), pos, original, mutated)
            inserted += 1
        print(f"[INFO] {fmt}: inserted {inserted} rows into {db_path.name}")

def main():
    if len(sys.argv) > 1:
        fmts = sys.argv[1:]
    else:
        fmts = ["date", "time", "url", "isbn", "ipv4", "ipv6", "pathfile"]
    for fmt in fmts:
        process_format(fmt)

if __name__ == "__main__":
    random.seed(1337)
    main()
