#!/usr/bin/env python3
"""Regression test for regression R80 -- the shared wikilink primitives in
scripts/_wikilib.py, and lint.py's use of them.

Two ad-hoc reimplementations of wikilink parsing, written in one afternoon,
produced two different wrong answers: the first read backticked [[examples]]
quoted inside an append-only log as live links (2 false findings filed as
vault work), the second resolved every target as `path + ".md"` and so called
[[../CLAUDE.md]] broken. lint.py knew both rules; nothing could import them.
Now iter_wikilinks/build_index/resolve are the one copy, and lint.py is their
first consumer -- so these cases are pinned end to end through lint, not just
at the library.

Cases (through `python scripts/lint.py` unless noted):
  W1  single-backticked `[[example]]` is not an edge and not a finding
  W2  double-backticked `` `[[example]]` `` likewise
  W3  a wikilink inside a fenced block likewise
  W4  [[../CLAUDE.md]] -- explicit .md -- resolves (bug 2's false positive);
      it is also wrong-depth here, so only RELATIVE-DEPTH may list it (R76)
  W5  control: a genuinely broken link IS still reported (the scan is not
      silently finding nothing, which would make W1-W4 vacuous)
  W6  a wrong-depth [[../../concepts/base]] that names a real page still
      resolves by basename (not in BROKEN -- the ladder stays
      Obsidian-faithful), and lint's separate RELATIVE-DEPTH section lists it
      as basename-rescued (regression R76)
  W7  a ../ target that exists on disk outside the vault lands in the
      CROSS-VAULT bucket, not in BROKEN
  W8  library-level: iter_wikilinks splits #heading / #^block-id / display
      text / the table-cell \\| escape, and resolve returns resolved,
      cross-vault and broken

Builds a throwaway two-vault fixture with the CURRENT scripts copied in.
Run: python scripts/tests/test_wikilib_parity.py    Exit 0 = pass.
"""
from __future__ import annotations

import re
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


PAGE = """---
title: Cases
updated: 2026-09-18
---

# Cases

W1 single-backticked example: `[[ghost-single]]` -- documentation, not an edge.
W2 double-backticked example: `` `[[ghost-double]]` `` -- same.
W3 fenced:

```
[[ghost-fenced]]
```

W4 explicit extension: [[../CLAUDE.md]] already carries the suffix.
W5 control: [[no-such-page-at-all]] really is broken.
W6 wrong depth, real page: [[../../concepts/base]].
W7 outside the vault: [[../../../VaultB/wiki/sources/shared]].
Display forms that must not change the target: [[concepts/base#Heading]],
[[concepts/base#^blk-1]], | [[concepts/base\\|Base]] | in a table cell.
"""

CLAUDE_MD = "# VaultA\n\nRoot page, so [[wiki/concepts/base]] has a linker.\n"


def build(base: Path) -> Path:
    va, vb = base / "VaultA", base / "VaultB"
    (va / "scripts").mkdir(parents=True)
    (va / "_meta").mkdir()
    (va / "wiki" / "concepts").mkdir(parents=True)
    (vb / "wiki" / "sources").mkdir(parents=True)
    for s in ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py"):
        shutil.copy2(SCRIPTS / s, va / "scripts" / s)
    (va / "CLAUDE.md").write_text(CLAUDE_MD, encoding="utf-8")
    (va / "wiki" / "concepts" / "base.md").write_text(
        "---\ntitle: Base\n---\n\n# Base\n\n## Heading\n\nbody\n", encoding="utf-8")
    (va / "wiki" / "concepts" / "cases.md").write_text(PAGE, encoding="utf-8")
    (vb / "wiki" / "sources" / "shared.md").write_text(
        "---\ntitle: Shared\n---\n\n# Shared\n", encoding="utf-8")
    return va


def run_lint(root: Path) -> str:
    r = subprocess.run([sys.executable, str(root / "scripts" / "lint.py")],
                       capture_output=True, text=True, errors="replace", cwd=str(root))
    return r.stdout + r.stderr


def count(out: str, section: str) -> int:
    m = re.search(r"=== " + re.escape(section) + r" ===+ (\d+)", out)
    return int(m.group(1)) if m else -1


def block(out: str, section: str) -> str:
    """The lines of one `=== <section> ...` block, up to the next `===` header."""
    m = re.search(r"^=== " + re.escape(section) + r"[^\n]*\n(.*?)(?=^===|\Z)",
                  out, re.M | re.S)
    return m.group(1) if m else ""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm80-"))
    try:
        va = build(tmp)
        out = run_lint(va)

        for case, ghost in (("W1", "ghost-single"), ("W2", "ghost-double"),
                            ("W3", "ghost-fenced")):
            check(f"[[{ghost}]]" not in out,
                  f"{case}: a code-span example was reported as a link:\n{out}")
        # W4 excludes the RELATIVE-DEPTH block: from wiki/concepts/ this
        # fixture link is also one level too shallow, which R76's section
        # rightly lists as basename-rescued. W4 pins the ladder (no false
        # BROKEN / CROSS-VAULT hit from the explicit .md), not the depth.
        check("[[../CLAUDE.md]]" not in out.replace(block(out, "RELATIVE-DEPTH LINKS"), ""),
              f"W4: explicit .md target reported as a finding:\n{out}")
        check("x1    [[no-such-page-at-all]]" in out or
              "[[no-such-page-at-all]]" in out,
              f"W5: the control broken link was NOT reported -- W1-W4 prove"
              f" nothing if the scan finds nothing:\n{out}")
        check(count(out, "BROKEN LINKS") == 1,
              f"W1-W5: expected exactly the one control break, got"
              f" {count(out, 'BROKEN LINKS')}:\n{out}")
        # W6 is scoped to the BROKEN block: since regression R76 the same link is
        # (correctly) listed by lint's separate RELATIVE-DEPTH section, which
        # must not change what the resolution ladder does.
        check("[[../../concepts/base]]" not in block(out, "BROKEN LINKS"),
              f"W6: basename fallback stopped rescuing a wrong-depth link --"
              f" the ladder must stay Obsidian-faithful:\n{out}")
        check("[[../../concepts/base]]" in block(out, "RELATIVE-DEPTH LINKS")
              and "basename-rescued" in block(out, "RELATIVE-DEPTH LINKS"),
              f"W6: the wrong-depth link is missing from RELATIVE-DEPTH (R76):\n{out}")
        check(count(out, "CROSS-VAULT LINKS (basename-unresolvable, verified on disk)") == 1
              and "[[../../../VaultB/wiki/sources/shared]]" in out,
              f"W7: the on-disk out-of-vault target missed the cross-vault"
              f" bucket:\n{out}")

        # W8 -- the library itself, independent of lint's bookkeeping.
        sys.path.insert(0, str(SCRIPTS))
        import _wikilib as wl

        links = list(wl.iter_wikilinks(
            "a `[[code]]` b [[page#Heading]] c [[page#^blk-1]] d [[page\\|Shown]]"))
        check([(l.target, l.anchor, l.display) for l in links] ==
              [("page", "Heading", ""), ("page", "^blk-1", ""), ("page", "", "Shown")],
              f"W8 iter_wikilinks: {links}")
        check(len(list(wl.iter_wikilinks("x `[[code]]` y", strip=False))) == 1,
              "W8 iter_wikilinks: strip=False must NOT drop code spans")

        texts = {p: (va / p).read_text(encoding="utf-8")
                 for p in ("wiki/concepts/base.md", "wiki/concepts/cases.md")}
        index = wl.build_index(list(texts), texts)
        here = "wiki/concepts/cases.md"
        cases = [
            ("base", ("resolved", ["wiki/concepts/base.md"])),
            ("BASE", ("resolved", ["wiki/concepts/base.md"])),   # case-insensitive
            ("concepts/base", ("resolved", ["wiki/concepts/base.md"])),
            ("../../concepts/base", ("resolved", ["wiki/concepts/base.md"])),
            ("../../../VaultB/wiki/sources/shared", ("cross-vault", [])),
            ("no-such-page-at-all", ("broken", [])),
            ("../../..", ("broken", [])),
        ]
        for target, want in cases:
            got = wl.resolve(target, here, index, root=str(va))
            check(got == want, f"W8 resolve({target!r}): got {got}, want {want}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        return 1
    print("PASS: code-span examples (single/double/fenced) are not findings, "
          "explicit .md resolves, the control break is still reported, "
          "basename fallback still rescues wrong-depth links and RELATIVE-DEPTH lists them (R76), "
          "on-disk out-of-vault targets stay in the cross-vault bucket, "
          "and iter_wikilinks/resolve behave at the library level")
    return 0


if __name__ == "__main__":
    sys.exit(main())
