#!/usr/bin/env python3
"""Regression test for regression R83 -- the documented scanner invocations must
not drop scripts/__pycache__ into a vault.

Wiki instances are cloud-synced (Dropbox) folders. Three template scripts
import a sibling module, which makes CPython write bytecode next to the
imported source -- i.e. inside the vault -- on every run of the close-out
command every session is told to type:

    lint.py            -> audit_claims, suggest_anchors  (locator hygiene)
    suggest_anchors.py -> audit_claims
    llm_suggest_links.py -> _wikilib

Each of the three now sets `sys.dont_write_bytecode = True` before its sibling
import. This test asserts that, and -- as a negative control -- that the SAME
runs against copies with the guard stripped DO create __pycache__, so a green
result can never mean "the import never happened".

Vacuity guards, in order of how badly they would bite:
  - lint.py wraps its locator-hygiene block in `except Exception` and still
    exits 0, so a failed sibling import would also produce no __pycache__.
    G1 therefore asserts the block RAN: hygiene line present, not "skipped",
    and carrying a coverage fraction over a non-zero denominator. The SHAPE,
    not a literal fraction -- the exact count is audit_claims' claim-detection
    heuristics' business, and this test's subject is bytecode.
  - the child env has PYTHONDONTWRITEBYTECODE and PYTHONPYCACHEPREFIX removed
    and gets no -B, so the run is exactly the unguarded documented one. (A
    stray PYCACHEPREFIX would otherwise send bytecode to a central dir and
    make every assertion here pass for the wrong reason.)

Run:  python scripts/tests/test_no_bytecode.py     (exit 0 = pass)
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
COPIED = ["lint.py", "audit_claims.py", "suggest_anchors.py", "_wikilib.py",
          "llm_suggest_links.py"]
GUARD = "sys.dont_write_bytecode = True"

SOURCE_PAGE = ("---\ntitle: Manual\ndescription: A source.\n---\n"
               "# Manual\n\n## Heading A\n\nbody\n")

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def claims_page(anchored: int, unanchored: int) -> str:
    lines = ["---", "title: Concept", "description: cites the manual.", "---",
             "# Concept", "", "## Notes", ""]
    for i in range(anchored):
        lines.append(f"- anchored claim {i} of 5 km per [[sources/manual#Heading A]]")
    for i in range(unanchored):
        lines.append(f"- bare claim {i} of 5 km per [[sources/manual]]")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_vault(base: Path, name: str, *, guarded: bool) -> tuple[Path, int]:
    """A throwaway vault carrying the scripts under test. guarded=False strips
    the regression R83 flag from every copy -- the negative control."""
    v = base / name
    (v / "scripts").mkdir(parents=True)
    (v / "wiki" / "sources").mkdir(parents=True)
    (v / "wiki" / "concepts").mkdir()
    (v / "_meta").mkdir()
    stripped = 0
    for s in COPIED:
        dst = v / "scripts" / s
        shutil.copy2(SCRIPTS / s, dst)
        if not guarded:
            text = dst.read_text(encoding="utf-8")
            if GUARD in text:
                stripped += text.count(GUARD)
                dst.write_text(
                    text.replace(GUARD, "pass  # guard stripped (control)"),
                    encoding="utf-8")
    (v / "wiki" / "sources" / "manual.md").write_text(SOURCE_PAGE, encoding="utf-8")
    (v / "wiki" / "concepts" / "concept.md").write_text(
        claims_page(3, 2), encoding="utf-8")
    return v, stripped


def child_env() -> dict:
    """The documented invocation's environment: no -B, and neither bytecode
    environment variable set, so the only thing suppressing a .pyc is the fix."""
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run(root: Path, script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / script), *args],
        capture_output=True, text=True, errors="replace",
        cwd=str(root), env=child_env())


def pycache(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix()
                  for p in root.rglob("*") if p.is_dir() and p.name == "__pycache__")


def main() -> int:
    # A failing child's stderr is printed with !r, which does NOT escape
    # printable non-ASCII (lint.py's own source carries em-dashes). On a
    # cp1252 console that would raise UnicodeEncodeError instead of printing
    # the FAIL line -- exactly when the message matters. Same idiom as help.py.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    with tempfile.TemporaryDirectory(prefix="wikillm83-") as td:
        base = Path(td)
        green, _ = build_vault(base, "guarded", guarded=True)

        # --- G1: the documented close-out command.
        p = run(green, "lint.py", "--summary")
        hyg = next((l for l in p.stdout.splitlines()
                    if "=== LOCATOR HYGIENE ===" in l), "")
        # Shape, not a literal count: `coverage N/M` with M > 0 proves the
        # sibling imports ran and measured real claims, and survives any
        # future tweak to what audit_claims counts as a claim.
        m = re.search(r"coverage (\d+)/(\d+)", hyg)
        ran = bool(hyg) and "skipped" not in hyg
        counted = bool(m) and int(m.group(2)) > 0
        check("G1a lint.py --summary actually ran the sibling-importing block",
              ran and counted,
              f"rc={p.returncode}; hygiene line={(hyg or '(absent)')!r}"
              f"; stderr={p.stderr.strip()[:200]!r}")
        check("G1b lint.py leaves no __pycache__ in the vault",
              p.returncode == 0 and pycache(green) == [],
              f"rc={p.returncode}; __pycache__ dirs={pycache(green)!r}")

        # --- G2: suggest_anchors run directly (imports audit_claims at module top).
        p = run(green, "suggest_anchors.py", "--summary")
        check("G2 suggest_anchors.py --summary leaves no __pycache__",
              p.returncode == 0 and pycache(green) == [],
              f"rc={p.returncode}; __pycache__ dirs={pycache(green)!r}; "
              f"stderr={p.stderr.strip()[:200]!r}")

        # --- G3: llm_suggest_links imports _wikilib at module top; --help is
        #     enough to exercise that import without touching a backend.
        p = run(green, "llm_suggest_links.py", "--help")
        check("G3 llm_suggest_links.py leaves no __pycache__ (_wikilib import)",
              p.returncode == 0 and pycache(green) == [],
              f"rc={p.returncode}; __pycache__ dirs={pycache(green)!r}; "
              f"stderr={p.stderr.strip()[:200]!r}")

        # --- R: negative controls. Same commands, guard stripped: bytecode MUST
        #     appear, or this whole test is measuring nothing.
        red, stripped = build_vault(base, "unguarded", guarded=False)
        check("R0 the control tree really had the guard stripped",
              stripped >= 3,
              f"{stripped} guard line(s) removed (one each from lint.py, "
              f"suggest_anchors.py, llm_suggest_links.py)")

        p = run(red, "lint.py", "--summary")
        red_lint = pycache(red)
        check("R1 control: unguarded lint.py DOES write __pycache__",
              red_lint != [], f"rc={p.returncode}; __pycache__ dirs={red_lint!r}")

        shutil.rmtree(red / "scripts" / "__pycache__", ignore_errors=True)
        p = run(red, "llm_suggest_links.py", "--help")
        red_llm = pycache(red)
        check("R2 control: unguarded llm_suggest_links.py DOES write __pycache__",
              red_llm != [], f"rc={p.returncode}; __pycache__ dirs={red_llm!r}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
