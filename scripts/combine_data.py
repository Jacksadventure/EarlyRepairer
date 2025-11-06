#!/usr/bin/env python3
"""
Combine per-category text data from data/<category> into one file per category.

Defaults:
- root: data
- output: data/combined
- ext: .txt
- header: off (no per-file headers)
- encoding: utf-8 (errors=ignore)

Usage:
  python3 scripts/combine_data.py --root data --output data/combined
  python3 scripts/combine_data.py --with-header
"""
from __future__ import annotations

import argparse
import os
import sys
import re
from glob import glob
from typing import List


def natural_key(s: str):
    # Split into digit/non-digit chunks for natural sorting
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def find_categories(root: str) -> List[str]:
    try:
        entries = os.listdir(root)
    except FileNotFoundError:
        print(f"[ERROR] Root directory not found: {root}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[ERROR] Failed to list root directory {root}: {e}", file=sys.stderr)
        return []

    cats = []
    for name in sorted(entries, key=natural_key):
        p = os.path.join(root, name)
        if os.path.isdir(p):
            cats.append(name)
    return cats


def collect_files(category_dir: str, ext: str) -> List[str]:
    # Normalize extension to start with dot
    if ext and not ext.startswith("."):
        ext = "." + ext
    pattern = os.path.join(category_dir, "**", f"*{ext}")
    files = glob(pattern, recursive=True)
    files = [f for f in files if os.path.isfile(f)]
    files.sort(key=natural_key)
    return files


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def combine_category(
    root: str,
    category: str,
    out_dir: str,
    ext: str,
    with_header: bool,
    encoding: str,
    errors: str,
) -> int:
    category_dir = os.path.join(root, category)
    files = collect_files(category_dir, ext)
    ensure_dir(out_dir)
    out_path = os.path.join(out_dir, f"{category}.txt")

    count = 0
    bytes_written = 0

    with open(out_path, "w", encoding=encoding, errors=errors) as out_f:
        for idx, fp in enumerate(files):
            rel = os.path.relpath(fp, root)
            if with_header:
                header = f"===== {rel} =====\n"
                out_f.write(header)
                bytes_written += len(header.encode(encoding, errors=errors))

            try:
                with open(fp, "r", encoding=encoding, errors=errors) as in_f:
                    content = in_f.read()
            except Exception as e:
                print(f"[WARN] Skip unreadable file {fp}: {e}", file=sys.stderr)
                continue

            # Ensure separation between files
            if content and not content.endswith("\n"):
                content += "\n"
            out_f.write(content)
            bytes_written += len(content.encode(encoding, errors=errors))
            count += 1


    print(f"[OK] {category}: {count} files -> {out_path} ({bytes_written} bytes)")
    return count


def parse_args():
    p = argparse.ArgumentParser(description="Combine per-category text files.")
    p.add_argument("--root", default="data", help="Root directory containing categories (default: data)")
    p.add_argument("--output", default=os.path.join("data", "combined"), help="Output directory (default: data/combined)")
    p.add_argument("--ext", default=".txt", help="File extension to include (default: .txt)")
    p.add_argument("--with-header", action="store_true", help="Include per-file header with relative path")
    p.add_argument("--encoding", default="utf-8", help="Text encoding for reading/writing (default: utf-8)")
    p.add_argument("--errors", default="ignore", help="Error handling for encoding (default: ignore)")
    p.add_argument("--dry-run", action="store_true", help="Only print what would be done")
    return p.parse_args()


def main():
    args = parse_args()

    if not os.path.isdir(args.root):
        print(f"[ERROR] Root directory does not exist: {args.root}", file=sys.stderr)
        return 2

    categories = find_categories(args.root)
    if not categories:
        print(f"[WARN] No categories found under {args.root}", file=sys.stderr)
        return 0

    print(f"[INFO] Categories: {', '.join(categories)}")
    print(f"[INFO] Output dir: {args.output}")
    print(f"[INFO] Extension: {args.ext}")
    if args.dry_run:
        print("[INFO] Dry-run mode enabled")

    total_files = 0
    for cat in categories:
        cat_dir = os.path.join(args.root, cat)
        if not os.path.isdir(cat_dir):
            continue
        files = collect_files(cat_dir, args.ext)
        if args.dry_run:
            print(f"[DRY] Would combine {len(files)} files for category '{cat}' -> {os.path.join(args.output, f'{cat}.txt')}")
            continue

        count = combine_category(
            root=args.root,
            category=cat,
            out_dir=args.output,
            ext=args.ext,
            with_header=args.with_header,
            encoding=args.encoding,
            errors=args.errors,
        )
        total_files += count

    print(f"[DONE] Combined {total_files} files across {len(categories)} categories")
    return 0


if __name__ == "__main__":
    sys.exit(main())
