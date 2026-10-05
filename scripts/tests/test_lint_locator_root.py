#!/usr/bin/env python3
"""Regression test for regression R101 -- lint.py's LOCATOR HYGIENE section
describes the cwd vault, not the script's own repo.

lint.py walks wiki/ + _meta/ from cwd, but its LOCATOR HYGIENE block imports
audit_claims / suggest_anchors, whose ROOT is derived from __file__. A
template or worktree copy run from a vault's root therefore printed the
VAULT's link inventory beside the TEMPLATE's locator coverage ("no
source-citing claims") in one report, with nothing saying they came from
different trees. The fix calls audit_claims.set_root(cwd) (regression R67)
before the scan and prints `  root: <cwd>` under the section header.

Layout: one fixture vault (2 source-citing claims, 1 anchored) with NO
scripts/ of its own, and one "repo" dir holding the scripts under test plus
an EMPTY wiki/ -- the template shape. lint.py is run from the repo dir with
cwd = fixture, exactly the R67 worktree-copy situation.

Cases:
  G1  fixed lint.py, cwd = fixture -> `coverage 1/2 (50%, target 60%)`
  G2  the line under the header names the fixture root, under --summary too
  G3  cwd = a directory with no wiki/ -> `=== LOCATOR HYGIENE === skipped
      (ValueError: ...)` still prints and the exit code equals the
      pre-fix script's exit code from the same cwd
  P1  every non-LOCATOR line of the report is byte-identical between the
      fixed script and the pre-fix mutant on the same fixture (the ruling:
      link-inventory counts must not move)
  P2  maintenance_preflight.parse_lint still reads the coverage line
  RED the mutant (the set_root call stripped -- the pre-R101 block) run from
      the SAME cwd reports the repo's `no source-citing claims`, i.e. two
      trees in one report

Run: python scripts/tests/test_lint_locator_root.py    Exit 0 = pass.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
SIBLINGS = ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py")
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def fm(title: str) -> str:
    return f"---\ntitle: {title}\nupdated: 2026-09-27\n---\n\n# {title}\n\n"


def build_fixture(base: Path) -> Path:
    v = base / "Vault"
    for d in ("_meta", "raw", "wiki/concepts", "wiki/sources"):
        (v / d).mkdir(parents=True)
    (v / "wiki" / "index.md").write_text(
        fm("Index") + "[[concepts/thing]] [[sources/paper]]\n", encoding="utf-8")
    (v / "wiki" / "sources" / "paper.md").write_text(
        fm("Paper") + "## Findings\n\nsome quote\n", encoding="utf-8")
    (v / "wiki" / "concepts" / "thing.md").write_text(
        fm("Thing")
        + "Anchored claim [[sources/paper#Findings]].\n"
        + "Bare claim [[sources/paper]].\n",
        encoding="utf-8")
    return v


def build_repo(base: Path, mutant: bool) -> Path:
    """The scripts under test in a template-shaped dir: scripts/ + an EMPTY
    wiki/ (the template's own population is zero claims)."""
    repo = base / ("repo-mutant" if mutant else "repo")
    (repo / "scripts").mkdir(parents=True)
    (repo / "wiki").mkdir()
    for s in SIBLINGS:
        shutil.copy2(SCRIPTS / s, repo / "scripts" / s)
    if mutant:
        p = repo / "scripts" / "lint.py"
        src = p.read_text(encoding="utf-8")
        # Strip the R101 rebinding only; the header/root print stays so the
        # mutant is the pre-fix BEHAVIOUR, not a syntax variant.
        mutated = src.replace("        root = sa.set_root(os.getcwd())\n",
                              "        root = sa.ac.ROOT\n")
        if mutated == src:
            raise SystemExit("mutant build failed: set_root call not found in lint.py")
        p.write_text(mutated, encoding="utf-8")
    return repo


def run_lint(repo: Path, cwd: Path, *extra: str) -> tuple[str, int]:
    r = subprocess.run([sys.executable, str(repo / "scripts" / "lint.py"), *extra],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(cwd))
    return r.stdout + r.stderr, r.returncode


def locator_line(out: str) -> str:
    m = re.search(r"^=== LOCATOR HYGIENE ===.*$", out, re.M)
    return m.group(0) if m else ""


def root_line(out: str) -> str:
    m = re.search(r"^  root: (.*)$", out, re.M)
    return m.group(1).strip() if m else ""


def non_locator(out: str) -> list[str]:
    keep, skip = [], False
    for line in out.splitlines():
        if line.startswith("=== LOCATOR HYGIENE"):
            skip = True
            continue
        if line.startswith("==="):
            skip = False
        if not skip:
            keep.append(line)
    return keep


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        vault = build_fixture(base)
        repo = build_repo(base, mutant=False)
        mut = build_repo(base, mutant=True)
        novault = base / "not-a-vault"
        novault.mkdir()

        # RED control -- the pre-fix block from a vault cwd describes the repo.
        red, red_rc = run_lint(mut, vault)
        red_loc = locator_line(red)
        check("no source-citing claims" in red_loc,
              f"RED control did not reproduce the bug: {red_loc!r}")
        print(f"RED  (mutant, cwd=fixture): {red_loc}")

        # G1 -- the fixed script counts the cwd vault's claims.
        out, rc = run_lint(repo, vault)
        loc = locator_line(out)
        check("coverage 1/2 (50%, target 60%)" in loc, f"G1 coverage line wrong: {loc!r}")
        check("broken heading anchors 0 | suspect 0" in loc, f"G1 anchor counts wrong: {loc!r}")
        check(rc == 0, f"G1 exit {rc}, expected 0")
        print(f"G1   (fixed,  cwd=fixture): {loc}")

        # G2 -- the root line names the fixture, in full and --summary output.
        check(Path(root_line(out)).resolve() == vault.resolve(),
              f"G2 root line {root_line(out)!r} != {vault}")
        summ, _ = run_lint(repo, vault, "--summary")
        check("coverage 1/2 (50%" in locator_line(summ), "G2 --summary lost the coverage line")
        check(Path(root_line(summ)).resolve() == vault.resolve(),
              f"G2 --summary root line {root_line(summ)!r}")
        print(f"G2   root line: {root_line(summ)}")

        # G3 -- a non-vault cwd still gets the advisory fallback, same exit code.
        nv, nv_rc = run_lint(repo, novault)
        nv_loc = locator_line(nv)
        check(nv_loc.startswith("=== LOCATOR HYGIENE === skipped (ValueError:"),
              f"G3 fallback line missing: {nv_loc!r}")
        check("no wiki/ directory" in nv_loc, f"G3 fallback does not name the cause: {nv_loc!r}")
        _, mut_nv_rc = run_lint(mut, novault)
        check(nv_rc == mut_nv_rc, f"G3 exit code moved: fixed {nv_rc} vs pre-fix {mut_nv_rc}")
        # The mutant never enters the skipped branch from this cwd, so the
        # equality above alone is 0 == 0; pin the skipped path's own exit.
        check(nv_rc == 0, f"G3 skipped section changed lint's exit code to {nv_rc}")
        check(root_line(nv) == "", "G3 printed a root line for a skipped section")
        print(f"G3   (fixed,  cwd=non-vault): {nv_loc}  exit {nv_rc} (pre-fix {mut_nv_rc})")

        # P1 -- link inventory byte-identical fixed vs mutant on the same cwd.
        check(non_locator(out) == non_locator(red),
              "P1 non-LOCATOR report lines differ between fixed and pre-fix script")
        summ_red, _ = run_lint(mut, vault, "--summary")
        check(non_locator(summ) == non_locator(summ_red),
              "P1 --summary non-LOCATOR lines differ between fixed and pre-fix script")
        print(f"P1   non-LOCATOR lines identical: {len(non_locator(out))} lines")

        # P2 -- preflight still parses the section.
        sys.path.insert(0, str(SCRIPTS))
        sys.dont_write_bytecode = True
        import maintenance_preflight as mp  # noqa: E402
        counts, reason = mp.parse_lint(out)
        check(reason is None, f"P2 parse_lint reason: {reason!r}")
        check(counts.get("anchor_eligible") == 2 and counts.get("anchor_covered") == 1,
              f"P2 parse_lint counts: {counts!r}")
        print(f"P2   parse_lint: covered {counts.get('anchor_covered')} / "
              f"eligible {counts.get('anchor_eligible')}, reason {reason!r}")

    if fails:
        print("\nFAIL")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("\nPASS test_lint_locator_root.py (regression R101)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
