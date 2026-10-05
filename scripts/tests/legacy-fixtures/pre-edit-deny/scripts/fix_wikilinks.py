#!/usr/bin/env python
"""One-shot fixup: strip backticks wrapping [[wikilinks]] across the vault.

Obsidian doesn't parse wikilinks inside code spans (backticks). Earlier
in the project, every wikilink was written as `[[foo]]` (in backticks for
visual emphasis), which silently broke all graph connections.

This script replaces `[[...]]` with [[...]] across .md files in:
- wiki/
- _meta/
- CLAUDE.md

Run:
    python scripts/fix_wikilinks.py            # dry-run, show counts only
    python scripts/fix_wikilinks.py --apply    # actually make the edits
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    PROJECT_ROOT / "wiki",
    PROJECT_ROOT / "_meta",
    PROJECT_ROOT / "CLAUDE.md",
]

PATTERN = re.compile(r"`(\[\[[^\]]+\]\])`")


def collect_md_files() -> list[Path]:
    files: list[Path] = []
    for t in TARGETS:
        if t.is_file() and t.suffix == ".md":
            files.append(t)
        elif t.is_dir():
            files.extend(p for p in t.rglob("*.md") if p.is_file())
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Actually write the changes (default is dry-run).")
    args = ap.parse_args()

    files = collect_md_files()
    total_replacements = 0
    files_changed = 0

    for f in files:
        text = f.read_text(encoding="utf-8")
        new_text, n = PATTERN.subn(r"\1", text)
        if n > 0:
            files_changed += 1
            total_replacements += n
            rel = f.relative_to(PROJECT_ROOT)
            print(f"  {n:4d}  {rel}")
            if args.apply:
                f.write_text(new_text, encoding="utf-8")

    print()
    if total_replacements == 0:
        print("No backtick-wrapped wikilinks found. Nothing to do.")
        return 0

    print(f"Total: {total_replacements} replacements across {files_changed} files.")
    if args.apply:
        print("Applied.")
    else:
        print("(Dry run. Re-run with --apply to write changes.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
