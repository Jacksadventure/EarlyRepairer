#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate double_xxx.db for data formats using a reguxtest-like approach:
- For each original file under data/<fmt>/, find two distinct positions (p1, p2)
  such that a single-character replacement at each position individually
  causes match.py validation to fail (ret != 0).
- Apply both replacements on the original (with index offset handling), ensure
  the combined result is also invalid, then store exactly ONE row per original.
- The DB schema is compatible with mutation_double.py:
    mutations(id INTEGER PK, file_path TEXT, mutation_pos TEXT, original_text TEXT, mutated_text TEXT)
  where mutation_pos is "p1,p2".
"""
import os
import sys
import sqlite3
import subprocess
import tempfile
import random
from pathlib import Path
from typing import Optional, Tuple

DIR_TO_CATEGORY = {
    "date": "Date",
    "ipv4": "IPv4",
    "ipv6": "IPv6",
    "isbn": "ISBN",
    "pathfile": "FilePath",
    "time": "Time",
    "url": "URL",
}

REPLACEMENTS = ["*", "#", "%", "!", "^", "$", "&"]

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
               mutation_pos  TEXT,           -- "p1,p2"
               original_text TEXT,
               mutated_text  TEXT
           );"""
    )
    conn.commit()

def validate_with_matchpy_text(category: str, text: str) -> int:
    """
    Write text to a temp file, run match.py CATEGORY <tmp>, return exit code.
    match.py exit codes:
      0  = full match
     -1  = partial match  (becomes 255 at OS level)
      1  = not matched
    For our purpose, 'invalid' is ret != 0.
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

def find_single_invalid_replacement_pos(s: str, category: str, exclude: set[int]) -> Optional[Tuple[int, str]]:
    """
    Return (pos, rep_char) where replacing s[pos] with rep_char yields invalid (ret != 0).
    Excludes positions in 'exclude'.
    """
    n = len(s)
    if n == 0:
        return None
    indices = list(range(n))
    random.shuffle(indices)
    for i in indices:
        if i in exclude:
            continue
        orig_ch = s[i]
        for rep in REPLACEMENTS:
            if rep == orig_ch:
                continue
            mutated = s[:i] + rep + s[i+1:]
            rc = validate_with_matchpy_text(category, mutated)
            if rc != 0:
                return i, rep
    return None

def apply_two_replacements(s: str, p1: int, r1: str, p2: int, r2: str) -> str:
    """
    Apply replacements at original indices p1 and p2 to the original string s.
    Handle ordering by applying at the smaller index first.
    """
    if p1 == p2:
        # Should not happen; caller ensures distinct positions
        return s
    if p1 > p2:
        p1, p2 = p2, p1
        r1, r2 = r2, r1
    # Replace at p1
    t = s[:p1] + r1 + s[p1+1:]
    # Adjust index for second replacement if needed (same length, so no shift)
    t = t[:p2] + r2 + t[p2+1:]
    return t

def find_double_mutation(s: str, fmt: str, max_tries: int = 1000) -> Optional[Tuple[str, Tuple[int, int]]]:
    """
    Try to find two distinct positions p1, p2 where single-character replacement at each
    individually leads to invalid, and the combined double replacement is also invalid.
    Returns (mutated_text, (p1, p2)) or None if not found.
    """
    category = DIR_TO_CATEGORY.get(fmt)
    if not category:
        return None

    # Quick exit for too short strings
    if len(s) == 0:
        # insert two characters at both ends as a fallback
        mutated = REPLACEMENTS[0] + REPLACEMENTS[1]
        rc = validate_with_matchpy_text(category, mutated)
        if rc != 0:
            return mutated, (0, 0)
        return None
    if len(s) == 1:
        # Use replacement at pos 0 and an insertion-like effect by changing twice (fallback: two different replacements)
        for r1 in REPLACEMENTS:
            if r1 == s[0]:
                continue
            for r2 in REPLACEMENTS:
                if r2 == s[0] or r2 == r1:
                    continue
                mutated = r2  # second "position" fallback, treat as if two edits
                mutated = r1  # overwrite; with single char we can't encode two positions cleanly
        # For pathological 1-char inputs, fall back to duplicating then editing
        s2 = s + s
        res1 = find_single_invalid_replacement_pos(s2, category, set())
        if not res1:
            return None
        p1, r1 = res1
        res2 = find_single_invalid_replacement_pos(s2, category, {p1})
        if not res2:
            return None
        p2, r2 = res2
        mutated = apply_two_replacements(s2, p1, r1, p2, r2)
        rc = validate_with_matchpy_text(category, mutated)
        if rc != 0:
            return mutated, (p1, p2)
        return None

    tries = 0
    while tries < max_tries:
        tries += 1
        # p1
        res1 = find_single_invalid_replacement_pos(s, category, set())
        if not res1:
            return None
        p1, r1 = res1
        # p2 != p1
        res2 = find_single_invalid_replacement_pos(s, category, {p1})
        if not res2:
            # retry a few times
            continue
        p2, r2 = res2

        mutated = apply_two_replacements(s, p1, r1, p2, r2)
        rc = validate_with_matchpy_text(category, mutated)
        if rc != 0:
            return mutated, (p1, p2)
    return None

def store_pair(conn: sqlite3.Connection,
               file_path: str,
               p1p2: Tuple[int, int],
               original_text: str,
               mutated_text: str) -> None:
    conn.execute(
        "INSERT INTO mutations (file_path, mutation_pos, original_text, mutated_text) VALUES (?, ?, ?, ?)",
        (file_path, f"{p1p2[0]},{p1p2[1]}", original_text, mutated_text),
    )
    conn.commit()

def process_format(fmt: str) -> None:
    fmt_dir = DATA_DIR / fmt
    if not fmt_dir.is_dir():
        print(f"[SKIP] data/{fmt} not found")
        return

    db_path = OUT_DIR / f"double_{fmt}.db"
    with sqlite3.connect(db_path) as conn:
        ensure_table(conn)
        files = sorted([p for p in fmt_dir.iterdir() if p.is_file()])
        inserted = 0
        for path in files:
            try:
                original = path.read_text(encoding="utf-8", errors="ignore").strip()
            except Exception:
                continue
            res = find_double_mutation(original, fmt)
            if not res:
                continue
            mutated, (p1, p2) = res
            store_pair(conn, str(path), (p1, p2), original, mutated)
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
    random.seed(4242)
    main()
