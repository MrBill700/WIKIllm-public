#!/usr/bin/env python3
"""Regression test for regression R91 -- suggest_anchors.py --apply on a line
that cites two sources.

The stale-line guard (an entry's recorded `line` must still prefix the page
line) compared against the line as ALREADY REWRITTEN by the first anchor in
the same batch, so the second citation on any multi-link line was always
refused as "line changed since the scan" (sample-vault-a, run wf_example:
submitted 43, applied 42, the one SKIP was structural). The guard now compares
against the line as the batch first found it.

Cases:
  M1  two different sources on one line, two entries -> both applied, 0 skipped
  M2  the same source cited twice on one line -> both occurrences anchored
  M3  a REAL external edit to the line (recorded prefix no longer matches the
      line as found) -> still SKIPped: the guard has not been weakened
  M4  entries without `line` (pre-R90 decisions files) -> still applied
  M5  byte transparency: a CRLF page stays CRLF after the edit

Builds a throwaway vault with the CURRENT scripts copied in (suggest_anchors
derives ROOT from __file__). Run: python scripts/tests/test_apply_multi_citation.py
Exit 0 = pass.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def build(root: Path, page_text: str, newline: str = "\n") -> None:
    (root / "scripts").mkdir(parents=True)
    for s in ("audit_claims.py", "suggest_anchors.py", "check_raw.py", "lint.py", "_wikilib.py"):
        if (SCRIPTS / s).exists():
            shutil.copy2(SCRIPTS / s, root / "scripts" / s)
    (root / "_meta").mkdir()
    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    for slug in ("alpha", "beta"):
        (src / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\n---\n\n# {slug}\n\n## Load-bearing section\n\nbody\n\n## Other section\n\nbody\n",
            encoding="utf-8")
    (root / "wiki" / "concepts").mkdir()
    with (root / "wiki" / "concepts" / "page.md").open("w", encoding="utf-8", newline="") as fh:
        fh.write(page_text.replace("\n", newline))


def run_apply(root: Path, entries: list[dict]) -> str:
    dec = root / "_meta" / "anchor-decisions-latest.json"
    dec.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    r = subprocess.run([sys.executable, str(root / "scripts" / "suggest_anchors.py"),
                        "--apply", str(dec), "--no-history"],
                       capture_output=True, text=True, errors="replace", cwd=str(root))
    return r.stdout + r.stderr


PAGE = ("---\ntitle: Page\n---\n\n# Page\n\n"
        "- Two-source claim per [[sources/alpha]] (uptake) and [[sources/beta]] (potting).\n"
        "- Same-source twice: [[sources/alpha]] then again [[sources/alpha]].\n"
        "- Single claim per [[sources/beta]].\n")
LINE7 = "- Two-source claim per [[sources/alpha]] (uptake) and [[sources/beta]] (potting)."
LINE8 = "- Same-source twice: [[sources/alpha]] then again [[sources/alpha]]."
LINE9 = "- Single claim per [[sources/beta]]."


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm91-"))
    try:
        # M1 + M2 + M4 in one apply
        root = tmp / "v1"
        build(root, PAGE)
        out = run_apply(root, [
            {"rel": "wiki/concepts/page.md", "lineno": 7, "slug": "alpha", "line": LINE7, "anchor": "Load-bearing section"},
            {"rel": "wiki/concepts/page.md", "lineno": 7, "slug": "beta", "line": LINE7, "anchor": "Other section"},
            {"rel": "wiki/concepts/page.md", "lineno": 8, "slug": "alpha", "line": LINE8, "anchor": "Load-bearing section"},
            {"rel": "wiki/concepts/page.md", "lineno": 8, "slug": "alpha", "line": LINE8, "anchor": "Other section"},
            {"rel": "wiki/concepts/page.md", "lineno": 9, "slug": "beta", "anchor": "Other section"},   # M4: no line key
        ])
        text = (root / "wiki" / "concepts" / "page.md").read_text(encoding="utf-8")
        check("=== APPLIED === 5 anchor(s)" in out and "; 0 skipped" in out, f"M1/M2/M4: {out}")
        check("[[sources/alpha#Load-bearing section]] (uptake) and [[sources/beta#Other section]] (potting)" in text,
              f"M1: second citation not anchored:\n{text}")
        check("[[sources/alpha#Load-bearing section]] then again [[sources/alpha#Other section]]" in text,
              f"M2: same-source twice:\n{text}")
        check("Single claim per [[sources/beta#Other section]]" in text, f"M4: entry without line not applied:\n{text}")
        check("line changed since the scan" not in out, f"M1: structural SKIP still fires: {out}")

        # M3: a real external edit still SKIPs (guard not weakened)
        root = tmp / "v2"
        build(root, PAGE.replace("Two-source claim", "Two-source claim EDITED"))
        out = run_apply(root, [
            {"rel": "wiki/concepts/page.md", "lineno": 7, "slug": "alpha", "line": LINE7, "anchor": "Load-bearing section"},
        ])
        text = (root / "wiki" / "concepts" / "page.md").read_text(encoding="utf-8")
        check("line changed since the scan" in out and "=== APPLIED === 0 anchor(s)" in out, f"M3: {out}")
        check("#Load-bearing section" not in text, f"M3: stale entry was written:\n{text}")

        # M5: CRLF page stays CRLF, both anchors land
        root = tmp / "v3"
        build(root, PAGE, newline="\r\n")
        out = run_apply(root, [
            {"rel": "wiki/concepts/page.md", "lineno": 7, "slug": "alpha", "line": LINE7, "anchor": "Load-bearing section"},
            {"rel": "wiki/concepts/page.md", "lineno": 7, "slug": "beta", "line": LINE7, "anchor": "Other section"},
        ])
        raw = (root / "wiki" / "concepts" / "page.md").read_bytes()
        check("=== APPLIED === 2 anchor(s)" in out, f"M5: {out}")
        check(raw.count(b"\r\n") == PAGE.count("\n") and b"\n" not in raw.replace(b"\r\n", b""),
              "M5: line terminators rewritten")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        return 1
    print("PASS: two sources on one line both anchored; same source twice anchored twice; "
          "real external edit still SKIPs; entries without line still apply; CRLF preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
