#!/usr/bin/env python
"""One-shot fixup: strip backticks wrapping [[wikilinks]] across the vault.

Obsidian doesn't parse wikilinks inside code spans (backticks). Earlier
in the project, every wikilink was written as `[[foo]]` (in backticks for
visual emphasis), which silently broke all graph connections.

This script replaces `[[...]]` with [[...]] across .md files in:
- wiki/
- _meta/
- CLAUDE.md

Skips wiki/log.md, append_only and auto_generated pages, reporting each reason.
Uses maintenance_preflight's edit-deny resolver (or an announced standalone fallback).

Run:
    python scripts/fix_wikilinks.py            # dry-run, show counts only
    python scripts/fix_wikilinks.py --apply    # actually make the edits
    python scripts/fix_wikilinks.py --root PATH  # explicitly target another vault
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
try:
    from maintenance_preflight import edit_deny, fm_flag_true
except ImportError:
    edit_deny = None

    def fm_flag_true(path: Path, key: str) -> bool | None:
        # Standalone copy: retain the resolver's conservative flag semantics.
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        end = text.find("\n---", 3) if text.startswith("---") else -1
        if end == -1:
            return False
        return any(line.lower().replace(" ", "").startswith(key + ":true")
                   for line in text[:end].splitlines())

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PATTERN = re.compile(r"`(\[\[[^\]]+\]\])`")


def collect_md_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for t in (root / "wiki", root / "_meta", root / "CLAUDE.md"):
        if t.is_file() and t.suffix == ".md":
            files.append(t)
        elif t.is_dir():
            files.extend(p for p in t.rglob("*.md") if p.is_file())
    return sorted(files)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="Actually write the changes (default is dry-run).")
    ap.add_argument("--root", type=Path, default=PROJECT_ROOT, help="Vault root (default: script's parent vault).")
    args = ap.parse_args()

    root = args.root.resolve()
    if not (root / "wiki").is_dir():
        ap.error(f"not a vault (missing wiki/): {root}")
    files = collect_md_files(root)
    denied = edit_deny(root) if edit_deny is not None else None
    if denied is None:
        print("Edit-deny: standalone fallback (maintenance_preflight import unavailable).")
    else:
        print("Edit-deny: maintenance_preflight resolver + auto_generated protection.")
    # Path keys retain Windows case-insensitive identity (e.g. wiki/LOG.md).
    denied_reasons = {}
    if denied:
        for reason, paths in (("edit_deny", denied["paths"]),
                              ("append_only", denied["by_rule"]["frontmatter"]),
                              ("unreadable", denied["unreadable"]),
                              ("path", denied["by_rule"]["path"])):
            denied_reasons.update({(root / p).resolve(): reason for p in paths})
    denied_reasons[(root / "wiki/log.md").resolve()] = "path"
    skipped = dict.fromkeys(("path", "append_only", "auto_generated", "unreadable", "edit_deny"), 0)
    total_replacements = 0
    files_changed = 0

    for f in files:
        rel = f.relative_to(root).as_posix()
        reason = denied_reasons.get(f.resolve())
        if reason is None:
            for flag in ("append_only", "auto_generated"):
                value = fm_flag_true(f, flag)
                if value is None or value:
                    reason = "unreadable" if value is None else flag
                    break
        if reason is not None:
            skipped[reason] += 1
            continue
        text = f.read_text(encoding="utf-8")
        new_text, n = PATTERN.subn(r"\1", text)
        if n > 0:
            files_changed += 1
            total_replacements += n
            print(f"  {n:4d}  {rel}")
            if args.apply:
                f.write_text(new_text, encoding="utf-8")

    print()
    print(f"Skipped: {sum(skipped.values())} files (" +
          ", ".join(f"{reason}={count}" for reason, count in skipped.items()) + ").")
    if total_replacements == 0:
        print("No backtick-wrapped wikilinks found in eligible files. Nothing to do.")
        return 0

    print(f"Total: {total_replacements} replacements across {files_changed} files.")
    if args.apply:
        print("Applied.")
    else:
        print("(Dry run. Re-run with --apply to write changes.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
