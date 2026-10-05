#!/usr/bin/env python3
"""help.py -- what can I actually do here? (generated, so it cannot drift)

The discoverability problem this solves: the toolset grows, the flags multiply,
and six weeks later nobody remembers what exists. The obvious fix -- a
hand-written index of options -- is the same mistake this wiki keeps learning
about elsewhere: a summary written *about* a source drifts away from it and
then quietly lies. So this reads the scripts themselves (their docstrings and
their argparse definitions) and prints what is actually there, right now.

Usage:
  python scripts/help.py            # the map: every script, purpose, key flags
  python scripts/help.py --full     # every flag of every script
  python scripts/help.py lint       # everything about one script
  python scripts/help.py --recipes  # just the common command sequences

Stdlib only. Read-only: it invokes each Python script with --help, which
argparse handles before any script body runs, so nothing is written or
changed. PowerShell scripts (.ps1) are never executed -- their comment header
is read as documentation instead.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Curated ordering + grouping. Anything on disk but not listed still shows up,
# under "other" -- so a new script is never invisible just because this list
# was not updated.
GROUPS = [
    ("The scanner stack -- run these; they are the safety net", [
        "check_raw.py", "lint.py", "check_stale.py", "audit_claims.py",
    ]),
    ("Content tools -- run when you want them", [
        "suggest_links.py", "llm_suggest_links.py", "fix_wikilinks.py",
        "extract_chapter.py", "graph_export.py", "screen_capture.ps1",
        "verify_capture.py", "ocr_sidecars.py",
    ]),
    ("Fleet", ["sync_from_template.py"]),
]

RECIPES = [
    ("Ingest loop (the everyday path)", [
        ("python scripts/check_raw.py --summary", "what is new in raw/?"),
        ("python scripts/check_raw.py --accept \"file.pdf\" \"other.pdf\"",
         "accept AFTER ingesting; one flag many values, or repeat the flag -- both accumulate"),
    ]),
    ("Session close-out (all four, every time)", [
        ("python scripts/lint.py --summary", "is the graph broken?"),
        ("python scripts/check_stale.py --summary", "is anything stale or overdue?"),
        ("python scripts/audit_claims.py --n 5", "are the citations faithful? + locator coverage"),
        ("python scripts/check_raw.py --summary", "did anything land in raw/ mid-session?"),
    ]),
    ("Faithfulness", [
        ("python scripts/audit_claims.py --n 25", "deeper quarterly pass"),
        ("python scripts/audit_claims.py --source SLUG", "audit one source's claims"),
        ("python scripts/audit_claims.py --mode random", "uniform sample instead of risk-weighted"),
        ("python scripts/audit_claims.py --target 75", "raise the locator-coverage bar"),
    ]),
    ("When a scanner reports a nonzero count", [
        ("<same command WITHOUT --summary>", "get the detail; never pipe to head -- an early-closed pipe kills Python (exit 255)"),
    ]),
    ("Fleet maintenance", [
        ("python scripts/sync_from_template.py", "dry run -- what has drifted?"),
        ("python scripts/sync_from_template.py --apply", "pull template updates in (overwrites local edits to synced files)"),
    ]),
]

# Flags worth surfacing in the compact view, per script. Everything else is
# reachable via --full or `help.py <script>`.
KEY_FLAGS = {
    "check_raw.py": ["--summary", "--accept", "--accept-all", "--init", "--duplicates"],
    "lint.py": ["--summary"],
    "check_stale.py": ["--summary"],
    "audit_claims.py": ["--n", "--source", "--mode", "--target", "--root", "--include-judged", "--record-verdict"],
    "suggest_anchors.py": ["--apply-auto", "--apply-all", "--apply", "--check", "--no-history", "--root"],
    "sync_from_template.py": ["--apply"],
    "screen_capture.ps1": ["-Pages", "-OutDir", "-DelaySec", "-WindowTitle", "-TurnKey"],
    "verify_capture.py": ["--out-dir", "--tesseract-cmd"],
    "ocr_sidecars.py": ["--out-dir", "--tesseract-cmd"],
}


def ps1_header(path: Path) -> str:
    """A .ps1's leading comment block, stripped of '# ' -- its doc surface."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    out = []
    for line in text.splitlines():
        if line.startswith("#"):
            out.append(re.sub(r"^#\s?", "", line))
        elif line.strip():
            break
    return "\n".join(out)


def purpose(path: Path) -> str:
    """First meaningful sentence of the module docstring."""
    if path.suffix == ".ps1":
        lines = [l.strip() for l in ps1_header(path).splitlines() if l.strip()]
        if not lines:
            return ""
        first = lines[0]
        if "--" in first and first.lower().startswith(path.name.lower()[:6]):
            first = first.split("--", 1)[1].strip()
        return first
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    m = re.search(r'^\s*(?:#![^\n]*\n)?\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', text, re.S)
    if not m:
        return ""
    body = m.group(1).strip()
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    if not lines:
        return ""
    first = lines[0]
    # docstrings here open "name.py -- purpose"; keep the purpose half
    if "--" in first and first.lower().startswith(path.name.lower()[:6]):
        first = first.split("--", 1)[1].strip()
    if len(first) < 25 and len(lines) > 1:
        first = first.rstrip(".") + " -- " + lines[1]
    return first


def help_text(path: Path) -> str:
    if path.suffix == ".ps1":
        return ps1_header(path)
    try:
        r = subprocess.run([sys.executable, str(path), "--help"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=25, cwd=str(HERE.parent))
        return r.stdout or r.stderr or ""
    except Exception as e:
        return f"(could not read --help: {e})"


def options_of(path: Path) -> list:
    """(flag, help) pairs parsed out of argparse's own --help output."""
    out, txt = [], help_text(path)
    tail = txt.split("options:", 1)[-1] if "options:" in txt else txt
    cur_flag, cur_help = None, []
    for line in tail.splitlines():
        m = re.match(r"^\s{2,}(-{1,2}[^\s,]+(?:\s+[A-Z_]+)?(?:,\s*-{1,2}[^\s,]+(?:\s+[A-Z_]+)?)*)\s*(.*)$", line)
        if m and line.startswith(("  -", "\t-")):
            if cur_flag:
                out.append((cur_flag, " ".join(cur_help).strip()))
            cur_flag = m.group(1).strip()
            cur_help = [m.group(2).strip()]
        elif cur_flag and line.strip():
            cur_help.append(line.strip())
    if cur_flag:
        out.append((cur_flag, " ".join(cur_help).strip()))
    return [(f, h) for f, h in out if not f.startswith("-h")]


def wrap(text: str, width: int, indent: str) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return ("\n" + indent).join(lines)


def print_recipes():
    print("=" * 78)
    print("COMMON RECIPES")
    print("=" * 78)
    for title, items in RECIPES:
        print(f"\n{title}")
        for cmd, why in items:
            print(f"  {cmd}")
            print(f"      {wrap(why, 68, '      ')}")


def main():
    ap = argparse.ArgumentParser(
        description="Generated map of this wiki's tooling -- reads the scripts, so it cannot go stale.")
    ap.add_argument("script", nargs="?", help="show everything about one script (e.g. 'lint' or 'lint.py')")
    ap.add_argument("--full", action="store_true", help="every flag of every script")
    ap.add_argument("--recipes", action="store_true", help="just the common command sequences")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    present = sorted(p.name for pat in ("*.py", "*.ps1") for p in HERE.glob(pat)
                     if p.name not in {"help.py", "_wikilib.py"})

    if args.script:
        name = args.script if args.script.endswith(".py") else args.script + ".py"
        if name not in present:
            stem = args.script.lower()[:-3] if args.script.endswith(".py") else args.script.lower()
            near = [p for p in present if stem in p]
            if len(near) == 1:
                name = near[0]          # unambiguous prefix: just use it
                print(f"(matched '{args.script}' -> {name})\n")
            else:
                print(f"No such script: {name}")
                if near:
                    print("Did you mean: " + ", ".join(near))
                print("\nAvailable: " + ", ".join(present))
                return 0
        p = HERE / name
        print("=" * 78)
        print(name.upper())
        print("=" * 78)
        print(f"\n{wrap(purpose(p), 76, '')}\n")
        print(help_text(p).strip())
        return 0

    if args.recipes:
        print_recipes()
        return 0

    listed = {n for _, names in GROUPS for n in names}
    groups = GROUPS + [("Other (present on disk, not yet grouped)",
                        [n for n in present if n not in listed])]

    print("=" * 78)
    print("WIKI TOOLING -- generated from the scripts in scripts/")
    print("=" * 78)
    print("\n  python scripts/help.py <name>   everything about one script")
    print("  python scripts/help.py --full   every flag       --recipes  command sequences")

    for title, names in groups:
        names = [n for n in names if n in present]
        if not names:
            continue
        print(f"\n{title}")
        print("-" * 78)
        for n in names:
            p = HERE / n
            print(f"\n  {n}")
            print(f"      {wrap(purpose(p), 68, '      ')}")
            opts = options_of(p)
            if args.full:
                for flag, h in opts:
                    print(f"        {flag:<22} {wrap(h, 44, ' ' * 31)}")
            else:
                keys = KEY_FLAGS.get(n)
                show = [(f, h) for f, h in opts
                        if not keys or any(f.split()[0].split(",")[0] == k for k in keys)]
                if show:
                    print("        " + "  ".join(f.split()[0].rstrip(",") for f, _ in show))
                elif opts:
                    print(f"        ({len(opts)} options -- see `help.py {n[:-3]}`)")

    missing = [n for n in present if n not in listed]
    if missing:
        print("\n" + "-" * 78)
        print("NOTE: the ungrouped scripts above are instance-owned or new to the template.")
    print()
    print_recipes()
    print("\n" + "-" * 78)
    print("Rule that bites people: never pipe these to `head` / `Select-Object -First N`.")
    print("An early-closed pipe kills Python mid-print and reports a false failure (exit 255).")
    print("Use --summary, then re-run without it for detail.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
