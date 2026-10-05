#!/usr/bin/env python3
"""Regression test for regression R67 -- --root on audit_claims.py / suggest_anchors.py.

Both scripts derive ROOT from __file__, so the TEMPLATE (or a worktree) copy
run from a vault's cwd scanned the TEMPLATE's own empty wiki/ and printed
"No citation-bearing claim lines found" -- exit 0, looked clean, exercised
nothing. Every verification of an anchor-stack change had to copy the script
into the vault first. --root re-points the paths instead.

The fixture vault deliberately does NOT get a scripts/ copy: these cases run
the scripts UNDER TEST from where they live, which is the whole point.

  R1  audit_claims --root <fixture> sees the fixture's claims
  R2  the same command WITHOUT --root, cwd = the fixture, does NOT
      (the silent-empty regression, pinned so it cannot come back)
  R3  suggest_anchors --root writes _meta/anchor-suggestions.json UNDER THE
      FIXTURE and leaves the script's own repo untouched -- the from-import
      trap: `from audit_claims import ROOT` snapshots the value, so a moved
      audit_claims ROOT would have this script scanning one vault and writing
      _meta/ into another
  R4  a --root with no wiki/ FAILS LOUD (exit 2, message), rather than
      reproducing the silent empty-population run the flag exists to kill
  R5  --root changes nothing else: the no-flag run of a copy installed IN the
      fixture reports the same population and coverage as the --root run
  R6  a relative --root resolves against cwd (paths are resolved, because
      relative_to(ROOT) needs an absolute root)
  R7  the history WRITE path follows --root too: without --no-history the
      coverage datapoint lands under the target vault
  R8  ... and the apply path's post-apply coverage write does the same,
      leaving the script's own repo untouched
  R9  a MIXED-VERSION install -- this suggest_anchors.py beside a pre-R67
      audit_claims.py with no set_root -- fails the same loud way (exit 2,
      naming the sync), not with a raw AttributeError traceback: cross-script
      imports are guarded at the IMPORTER, not by sync ordering

Run: python scripts/tests/test_root_flag.py    Exit 0 = pass.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
AUDIT = SCRIPTS / "audit_claims.py"
ANCHORS = SCRIPTS / "suggest_anchors.py"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def build(root: Path) -> None:
    """A minimal vault: three source-citing claim lines, one already anchored."""
    (root / "_meta").mkdir(parents=True)
    (root / "raw").mkdir()
    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    (src / "handbook.md").write_text(
        "---\ntitle: Handbook\n---\n\n# Handbook\n\n"
        "## Threshold table\n\nThe stated ceiling is 42 kg per crate.\n\n"
        "## Storage notes\n\nKeep crates dry.\n",
        encoding="utf-8")
    con = root / "wiki" / "concepts"
    con.mkdir()
    (con / "crates.md").write_text(
        "---\ntitle: Crates\n---\n\n# Crates\n\n"
        "- ROOTFIXTURE ceiling is 42 kg per crate [[sources/handbook]].\n"
        "- ROOTFIXTURE storage is dry-only [[sources/handbook]].\n"
        "- ROOTFIXTURE anchored claim [[sources/handbook#Storage notes]].\n",
        encoding="utf-8")


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, errors="replace", cwd=str(cwd))


def population(out: str):
    m = re.search(r"population=(\d+)", out)
    return int(m.group(1)) if m else None


def coverage(out: str):
    m = re.search(r"  (\d+)/(\d+) \(\d+%\) carry a #heading anchor", out)
    return m.group(0).strip() if m else None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm67-"))
    try:
        vault = tmp / "vault"
        build(vault)

        # R1 -- the script under test, run from its own repo, against the vault
        r1 = run([sys.executable, str(AUDIT), "--root", str(vault), "--n", "3", "--no-history"], SCRIPTS)
        check(r1.returncode == 0, f"R1: exit {r1.returncode}\n{r1.stdout}{r1.stderr}")
        check(population(r1.stdout) == 3, f"R1: population != 3\n{r1.stdout}")
        check("ROOTFIXTURE" in r1.stdout, f"R1: fixture claims not in the queue\n{r1.stdout}")
        check(coverage(r1.stdout) == "1/3 (33%) carry a #heading anchor",
              f"R1: coverage line {coverage(r1.stdout)!r}\n{r1.stdout}")

        # R2 -- no --root: the vault's cwd is ignored, the script's own repo is
        # scanned. Asserted on the fixture's marker, not on what the template
        # happens to contain, so template content cannot break this.
        r2 = run([sys.executable, str(AUDIT), "--n", "3", "--no-history"], vault)
        check("ROOTFIXTURE" not in r2.stdout,
              f"R2: cwd alone now reaches the vault -- assertion stale?\n{r2.stdout}")

        # R3 -- suggest_anchors writes under the fixture, not under its own repo
        own = SCRIPTS.parent / "_meta" / "anchor-suggestions.json"
        before = own.read_bytes() if own.exists() else None
        r3 = run([sys.executable, str(ANCHORS), "--root", str(vault), "--summary"], SCRIPTS)
        after = own.read_bytes() if own.exists() else None
        check("ANCHOR SUGGESTIONS" in r3.stdout, f"R3: no scan output\n{r3.stdout}{r3.stderr}")
        check((vault / "_meta" / "anchor-suggestions.json").exists(),
              f"R3: fixture _meta/anchor-suggestions.json not written\n{r3.stdout}")
        check(before == after, "R3: the script's OWN _meta/anchor-suggestions.json moved -- "
                               "the from-import snapshot is back")
        check("unanchored claims: 2" in r3.stdout, f"R3: wrong population\n{r3.stdout}")

        # R4 -- fail loud, both scripts
        empty = tmp / "not-a-vault"
        empty.mkdir()
        for name, script in (("audit_claims", AUDIT), ("suggest_anchors", ANCHORS)):
            r4 = run([sys.executable, str(script), "--root", str(empty), "--no-history"], SCRIPTS)
            check(r4.returncode == 2, f"R4 {name}: exit {r4.returncode}, want 2\n{r4.stdout}{r4.stderr}")
            check("no wiki/" in r4.stderr, f"R4 {name}: stderr {r4.stderr!r}")
            check("No citation-bearing claim lines found" not in r4.stdout,
                  f"R4 {name}: a bad --root printed the silent-empty message\n{r4.stdout}")

        # R5 -- equivalence with the copy-into-the-vault pattern --root replaces
        (vault / "scripts").mkdir()
        for s in ("audit_claims.py", "suggest_anchors.py"):
            shutil.copy2(SCRIPTS / s, vault / "scripts" / s)
        r5 = run([sys.executable, str(vault / "scripts" / "audit_claims.py"),
                  "--n", "3", "--no-history"], vault)
        check(population(r5.stdout) == population(r1.stdout) and coverage(r5.stdout) == coverage(r1.stdout),
              f"R5: installed copy disagrees with --root\n--root: {population(r1.stdout)} {coverage(r1.stdout)}\n"
              f"installed: {population(r5.stdout)} {coverage(r5.stdout)}")

        # R6 -- a relative --root resolves against cwd
        r6 = run([sys.executable, str(AUDIT), "--root", "vault", "--n", "3", "--no-history"], tmp)
        check(r6.returncode == 0 and population(r6.stdout) == 3,
              f"R6: relative --root\n{r6.stdout}{r6.stderr}")

        # R7/R8 -- the WRITE path, without --no-history. COVERAGE_FILE is the
        # one relocated global that nothing else drags along (META and
        # SUGGESTIONS_FILE are re-derived in suggest_anchors.set_root), and a
        # history write landing in the script's own repo -- or in the wrong
        # vault -- is the same class of bug as the from-import snapshot.
        # refresh_coverage() only runs on an APPLY path, so R8 applies.
        cov_own = SCRIPTS.parent / "_meta" / "locator-coverage.json"
        cov_before = cov_own.read_bytes() if cov_own.exists() else None
        fresh = tmp / "vault2"
        build(fresh)
        r7 = run([sys.executable, str(AUDIT), "--root", str(fresh), "--n", "3"], SCRIPTS)
        check((fresh / "_meta" / "locator-coverage.json").exists(),
              f"R7: coverage history not written under --root\n{r7.stdout}{r7.stderr}")
        r8 = run([sys.executable, str(ANCHORS), "--root", str(fresh), "--apply-auto"], SCRIPTS)
        check("coverage now:" in r8.stdout, f"R8: no post-apply coverage line\n{r8.stdout}{r8.stderr}")
        check((cov_own.read_bytes() if cov_own.exists() else None) == cov_before,
              "R7/R8: the coverage history of the script's OWN repo moved -- "
              "a --root run is writing into the wrong vault")

        # R9 -- mixed-version install. The "old" audit_claims is this one with
        # set_root renamed out of existence: same API in every other respect,
        # which is exactly what a pre-R67 vault copy is from this script's
        # point of view, and it needs no git and no network to build.
        mixed = tmp / "mixed"
        (mixed / "scripts").mkdir(parents=True)
        old_audit = AUDIT.read_text(encoding="utf-8").replace(
            "\ndef set_root(", "\ndef _pre67_no_set_root(", 1)
        check("def _pre67_no_set_root(" in old_audit,
              "R9: could not synthesize a pre-R67 audit_claims -- did set_root move?")
        (mixed / "scripts" / "audit_claims.py").write_text(old_audit, encoding="utf-8")
        shutil.copy2(ANCHORS, mixed / "scripts" / "suggest_anchors.py")
        target = tmp / "vault3"
        build(target)
        r9 = run([sys.executable, str(mixed / "scripts" / "suggest_anchors.py"),
                  "--root", str(target), "--summary", "--no-history"], mixed)
        check(r9.returncode == 2,
              f"R9: exit {r9.returncode}, want 2\n{r9.stdout}{r9.stderr}")
        check("AttributeError" not in r9.stderr,
              f"R9: raw traceback instead of the guarded message\n{r9.stderr}")
        check("sync_from_template" in r9.stderr and "regression R67" in r9.stderr,
              f"R9: the message does not say how to fix it\n{r9.stderr}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        return 1
    print("PASS: --root scans the named vault (audit_claims + suggest_anchors), cwd alone still does not, "
          "suggest_anchors writes _meta/ under the target and not under its own repo, a rootless directory "
          "exits 2 instead of printing an empty population, --root matches an installed copy, relative paths "
          "resolve, the coverage-history writes follow --root instead of the script's own repo, and a "
          "mixed-version install (pre-R67 audit_claims) exits 2 with the sync instruction rather than "
          "an AttributeError traceback")
    return 0


if __name__ == "__main__":
    sys.exit(main())
