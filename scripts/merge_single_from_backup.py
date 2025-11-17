#!/usr/bin/env python3
import argparse
import os
import sqlite3
import shutil
import sys
import time
from typing import Dict, Tuple, Any, List, Optional

# Keys
FullKey = Tuple[str, int, int, str]   # (format, file_id, corrupted_index, algorithm)
ShortKey = Tuple[int, str]            # (file_id, algorithm)

# Columns to migrate from backup into target
COLUMNS_TO_COPY = [
    "repaired_text",
    "fixed",
    "iterations",
    "repair_time",
    "correct_runs",
    "incorrect_runs",
    "incomplete_runs",
    "distance_original_broken",
    "distance_broken_repaired",
    "distance_original_repaired",
]

# Columns involved in identifying rows
IDENT_COLS = ["format", "file_id", "corrupted_index", "algorithm"]


def fetch_results(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute("""
        SELECT id, format, file_id, corrupted_index, algorithm,
               repaired_text, fixed, iterations, repair_time,
               correct_runs, incorrect_runs, incomplete_runs,
               distance_original_broken, distance_broken_repaired, distance_original_repaired
        FROM results
    """)
    rows = cur.fetchall()
    return rows


def build_backup_indexes(rows: List[sqlite3.Row]):
    # Exact key index
    by_full: Dict[FullKey, sqlite3.Row] = {}
    # Short key index
    multi_by_short: Dict[ShortKey, List[sqlite3.Row]] = {}

    for r in rows:
        fk: FullKey = (r["format"], r["file_id"], r["corrupted_index"], r["algorithm"])
        by_full[fk] = r
        sk: ShortKey = (r["file_id"], r["algorithm"])
        multi_by_short.setdefault(sk, []).append(r)

    # Reduce multi_by_short to unique only mapping; ambiguous if more than one
    unique_by_short: Dict[ShortKey, sqlite3.Row] = {}
    ambiguous_shorts: Dict[ShortKey, int] = {}
    for sk, lst in multi_by_short.items():
        if len(lst) == 1:
            unique_by_short[sk] = lst[0]
        else:
            ambiguous_shorts[sk] = len(lst)
    return by_full, unique_by_short, ambiguous_shorts


def ensure_backup(target_path: str) -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = f"{target_path}.before_merge_{ts}.bak"
    shutil.copyfile(target_path, backup_path)
    return backup_path


def merge(target_db: str, backup_db: str, dry_run: bool = True) -> None:
    if not os.path.exists(target_db):
        print(f"[ERROR] Target DB not found: {target_db}")
        sys.exit(1)
    if not os.path.exists(backup_db):
        print(f"[ERROR] Backup DB not found: {backup_db}")
        sys.exit(1)

    tgt_conn = sqlite3.connect(target_db)
    tgt_conn.row_factory = sqlite3.Row
    bkp_conn = sqlite3.connect(backup_db)
    bkp_conn.row_factory = sqlite3.Row

    try:
        tgt_rows = fetch_results(tgt_conn)
        bkp_rows = fetch_results(bkp_conn)
        print(f"[INFO] Loaded {len(tgt_rows)} target rows from {target_db}")
        print(f"[INFO] Loaded {len(bkp_rows)} backup rows from {backup_db}")

        bkp_by_full, bkp_by_short_unique, ambiguous_shorts = build_backup_indexes(bkp_rows)
        if ambiguous_shorts:
            print(f"[WARN] Found {len(ambiguous_shorts)} ambiguous (file_id, algorithm) keys in backup; "
                  f"these will NOT be used for short-key fallback.")
            # Show up to 5 examples
            shown = 0
            for sk, n in ambiguous_shorts.items():
                print(f"  - (file_id={sk[0]}, algorithm={sk[1]}) has {n} backup rows")
                shown += 1
                if shown >= 5:
                    break

        updates_full = 0
        updates_short = 0
        no_match = 0

        sample_logs: List[str] = []

        if not dry_run:
            backup_path = ensure_backup(target_db)
            print(f"[INFO] Created safety backup of target DB at: {backup_path}")

        cur = tgt_conn.cursor()

        for r in tgt_rows:
            fk: FullKey = (r["format"], r["file_id"], r["corrupted_index"], r["algorithm"])
            sk: ShortKey = (r["file_id"], r["algorithm"])

            src: Optional[sqlite3.Row] = None
            match_type = None

            # Prefer exact full-key match
            if fk in bkp_by_full:
                src = bkp_by_full[fk]
                match_type = "full"
            # Fallback to unique short-key (file_id, algorithm)
            elif sk in bkp_by_short_unique:
                src = bkp_by_short_unique[sk]
                match_type = "short"

            if src is None:
                no_match += 1
                continue

            # Prepare values to copy
            vals = [src[col] for col in COLUMNS_TO_COPY]

            if not dry_run:
                # Update corresponding row in target by id
                set_clause = ", ".join([f"{col} = ?" for col in COLUMNS_TO_COPY])
                cur.execute(f"UPDATE results SET {set_clause} WHERE id = ?", (*vals, r["id"]))

            if match_type == "full":
                updates_full += 1
            else:
                updates_short += 1

            if len(sample_logs) < 10:
                sample_logs.append(
                    f"Updated target id={r['id']} via {match_type}-key "
                    f"(fmt={r['format']}, file_id={r['file_id']}, ci={r['corrupted_index']}, alg={r['algorithm']})"
                )

        if not dry_run:
            tgt_conn.commit()

        print("[SUMMARY] Merge from backup completed (dry-run)" if dry_run else "[SUMMARY] Merge from backup committed")
        print(f"  - Full-key updates:   {updates_full}")
        print(f"  - Short-key updates:  {updates_short}")
        print(f"  - No match in backup: {no_match}")
        if sample_logs:
            print("  - Sample updates:")
            for s in sample_logs:
                print(f"    * {s}")

    finally:
        bkp_conn.close()
        tgt_conn.close()


def main():
    parser = argparse.ArgumentParser(description="Merge results from single.bk.db into single.db using (file_id, algorithm) and exact (format, file_id, corrupted_index, algorithm) when possible.")
    parser.add_argument("--target", default="single.db", help="Target DB to fill (default: single.db)")
    parser.add_argument("--backup", default="single.bk.db", help="Backup DB to read from (default: single.bk.db)")
    parser.add_argument("--commit", action="store_true", help="Actually write updates (default: dry-run)")
    args = parser.parse_args()

    merge(args.target, args.backup, dry_run=(not args.commit))


if __name__ == "__main__":
    main()
