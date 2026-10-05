#!/usr/bin/env python3
"""Regression test for regression R76 -- lint.py's RELATIVE-DEPTH LINKS section.

The resolution ladder resolves a link by basename first, so a wrong-depth
`../` path whose last segment names ANY local page was rescued silently:
sample-vault-e's `[[../sibling-vault/wiki/start-here]]` (one level
too shallow) survived every lint run from 2026-05-22 to 2026-09-17 because the
vault has its own wiki/start-here.md, and 166 `../` links in its wiki/log.md
are still one level too high with lint reporting 0. The fix is a separate,
purely additive section that checks every `../` target on disk relative to
the citing file.

Cases (one two-vault fixture, the CURRENT scripts copied in, lint run from the
vault root exactly as the fleet runs it):
  R1  the issue's literal repro [[../wrong/depth/start-here]] in
      wiki/start-here.md -> reported, inside vault, basename-rescued
  R2  the sample-vault-e shape: a sibling vault's start-here one level too
      shallow ([[../VaultE/wiki/start-here]] from wiki/ lands INSIDE this
      vault, colliding with the local start-here) -> reported, inside vault,
      basename-rescued; its #heading / |display are stripped. R2b: the same
      target with one `../` too many escapes the vault -> escapes vault, rescued
  R3  [[../index]] from wiki/log.md (wrong) -> reported; the SAME target from
      wiki/entities/kid.md (right) -> not reported (grouping is per file)
  R4  an escaping ../ target that exists nowhere and names no page ->
      reported as "also broken", and BROKEN LINKS counts it as before
  R5  a ../ path that lands on a DIRECTORY does not count as existing (a
      link names a file) -> reported, also broken
  R6  a wrong-depth ../ attachment embed whose basename is a real asset ->
      reported, basename-rescued; the right-depth embed is not
  N1  the sibling vault's start-here at the RIGHT depth -> not reported
      (with and without an explicit .md)
  N2  a real cross-vault link with a unique basename -> not reported, and
      still counted in CROSS-VAULT
  N3  a backticked `[[../bad/depth/x]]` is documentation -> not reported
  N4  plain vault-relative [[entities/kid]] (a `/` but no `../`) -> not
      reported (the triage measured thousands of these per vault). N4b: the
      same link from wiki/entities/kid.md, where it does NOT exist relative
      to the citing file -- the case that kills an any-`/` trigger (a
      mutation to `"/" in target` survived the suite without it)
  P1  parity: ALL FILES / BROKEN / CROSS-VAULT / ORPHANS / GHOST are the
      pre-R76 numbers for this fixture (measured against the legacy fixture
      pre-sync-sweep (label 4d11a71) lint)
  P2  --summary prints the count line and no per-item rows
  P3  maintenance_preflight.parse_lint reads the count as the OPTIONAL key
      relative_depth_links, and returns the SAME reason with the line removed

RED control: run this file against a lint.py without the section (e.g. the
legacy fixture pre-sync-sweep, label 4d11a71) --
R1-R6 and P2/P3 fail.

Run: python scripts/tests/test_relative_depth.py    Exit 0 = pass.
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
HEADER = "RELATIVE-DEPTH LINKS (../ that resolve to nothing on disk)"


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def fm(title: str) -> str:
    return f"---\ntitle: {title}\nupdated: 2026-09-26\n---\n\n# {title}\n\n"


def build(base: Path) -> Path:
    # Parent/ holds two sibling vaults, like a cloud-sync folder holding several wikis.
    vp, ve = base / "Parent" / "VaultP", base / "Parent" / "VaultE"
    for d in ("scripts", "_meta", "raw", "wiki/entities"):
        (vp / d).mkdir(parents=True, exist_ok=True)
    (ve / "wiki" / "sources").mkdir(parents=True)
    for s in ("lint.py", "_wikilib.py", "audit_claims.py", "suggest_anchors.py"):
        shutil.copy2(SCRIPTS / s, vp / "scripts" / s)
    (vp / "raw" / "chart.png").write_bytes(b"\x89PNG\r\n")
    (vp / "CLAUDE.md").write_text("# VaultP\n\n[[wiki/start-here]] [[index]]\n",
                                  encoding="utf-8")
    (vp / "wiki" / "index.md").write_text(
        fm("Index") + "[[start-here]] [[entities/kid]] [[log]]\n", encoding="utf-8")
    (vp / "wiki" / "start-here.md").write_text(
        fm("Start here")
        + "R1 [[../wrong/depth/start-here]]\n"
        + "R2 [[../VaultE/wiki/start-here#Heading|sibling start]]\n"
        + "R2b [[../../../VaultE/wiki/start-here]]\n"
        + "N1 [[../../VaultE/wiki/start-here]] and [[../../VaultE/wiki/start-here.md]]\n"
        + "N2 [[../../VaultE/wiki/sources/shared]]\n"
        + "N3 `[[../bad/depth/x]]` is an example.\n"
        + "N4 [[entities/kid]]\n",
        encoding="utf-8")
    (vp / "wiki" / "log.md").write_text(
        fm("Log")
        + "R3 [[../index]] twice: [[../index]]\n"
        + "R4 [[../../../nowhere/page-x]]\n"
        + "R5 [[../wiki]]\n"
        + "R6 ![[../../raw/chart.png]] (wrong) and ![[../raw/chart.png]] (right)\n",
        encoding="utf-8")
    (vp / "wiki" / "entities" / "kid.md").write_text(
        fm("Kid") + "R3 right depth from here: [[../index]]\n"
        + "N4b vault-relative, not file-relative: [[entities/kid]]\n", encoding="utf-8")
    (ve / "wiki" / "start-here.md").write_text(fm("sibling start"), encoding="utf-8")
    (ve / "wiki" / "sources" / "shared.md").write_text(fm("Shared"), encoding="utf-8")
    return vp


def run_lint(root: Path, *extra: str) -> str:
    r = subprocess.run([sys.executable, str(root / "scripts" / "lint.py"), *extra],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(root))
    return r.stdout + r.stderr


def count(out: str, section: str) -> int:
    m = re.search(r"^=== " + re.escape(section) + r" ===+ (\d+)", out, re.M)
    return int(m.group(1)) if m else -1


def block(out: str, section: str) -> str:
    m = re.search(r"^=== " + re.escape(section) + r"[^\n]*\n(.*?)(?=^===|\Z)",
                  out, re.M | re.S)
    return m.group(1) if m else ""


def row(rd: str, target: str, src: str) -> str:
    """The one listing row for (target, src), or ''."""
    for line in rd.splitlines():
        if f"[[{target}]]  ({src})" in line:
            return line
    return ""


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm76-"))
    try:
        vp = build(tmp)
        out = run_lint(vp)
        rd = block(out, HEADER)

        want = {  # (target, src) -> (count, where, how)
            ("../wrong/depth/start-here", "wiki/start-here.md"): (1, "inside vault", "basename-rescued"),
            ("../VaultE/wiki/start-here", "wiki/start-here.md"): (1, "inside vault", "basename-rescued"),
            ("../../../VaultE/wiki/start-here", "wiki/start-here.md"): (1, "escapes vault", "basename-rescued"),
            ("../index", "wiki/log.md"): (2, "inside vault", "basename-rescued"),
            ("../../../nowhere/page-x", "wiki/log.md"): (1, "escapes vault", "also broken"),
            ("../wiki", "wiki/log.md"): (1, "inside vault", "also broken"),
            ("../../raw/chart.png", "wiki/log.md"): (1, "escapes vault", "basename-rescued"),
        }
        for (tgt, src), (n, where, how) in want.items():
            line = row(rd, tgt, src)
            check(bool(line) and line.lstrip().startswith(f"x{n} ")
                  and f"[{where}; {how}]" in line,
                  f"R: expected x{n} [[{tgt}]] ({src}) [{where}; {how}], got {line!r}\n{out}")
        for tgt, src in (("../index", "wiki/entities/kid.md"),
                         ("../../VaultE/wiki/start-here", "wiki/start-here.md"),
                         ("../../VaultE/wiki/start-here.md", "wiki/start-here.md"),
                         ("../../VaultE/wiki/sources/shared", "wiki/start-here.md"),
                         ("../bad/depth/x", "wiki/start-here.md"),
                         ("entities/kid", "wiki/start-here.md"),
                         ("entities/kid", "wiki/entities/kid.md"),
                         ("../raw/chart.png", "wiki/log.md")):
            check(not row(rd, tgt, src),
                  f"N: [[{tgt}]] ({src}) must NOT be reported:\n{out}")
        check(count(out, HEADER) == 8,
              f"R: expected 8 relative-depth occurrences, got {count(out, HEADER)}\n{out}")
        check(re.search(re.escape(HEADER) + r" === 8 \(escape vault 3, inside vault 5;"
                        r" basename-rescued 6, also broken 2\)", out) is not None,
              f"R: sub-count breakdown wrong:\n{out}")

        # P1 -- the pre-R76 numbers for this fixture, measured by running the
        # legacy fixture pre-sync-sweep (label 4d11a71) lint.py on it. Nothing here may move.
        for section, n in (("ALL FILES", 5), ("BROKEN LINKS", 2),
                           ("CROSS-VAULT LINKS (basename-unresolvable, verified on disk)", 1),
                           ("ORPHANS (zero inbound wikilinks)", 1),
                           ("GHOST/PLACEHOLDER LINKS", 0)):
            check(count(out, section) == n,
                  f"P1 parity: {section} = {count(out, section)}, expected {n}\n{out}")
        check("(2 distinct targets)" in out, f"P1 parity: BROKEN distinct targets:\n{out}")

        # P2 -- --summary keeps the count line, drops the rows.
        summ = run_lint(vp, "--summary")
        check(count(summ, HEADER) == 8 and "[[" not in block(summ, HEADER),
              f"P2: --summary line/rows wrong:\n{summ}")

        # P3 -- preflight: optional key, never a blocker.
        sys.path.insert(0, str(SCRIPTS))
        sys.dont_write_bytecode = True
        from maintenance_preflight import parse_lint
        counts, why = parse_lint(summ)
        check(counts.get("relative_depth_links") == 8,
              f"P3: parse_lint relative_depth_links = {counts.get('relative_depth_links')}")
        stripped = "\n".join(l for l in summ.splitlines()
                             if not l.startswith("=== RELATIVE-DEPTH"))
        counts2, why2 = parse_lint(stripped)
        check(why2 == why and "relative_depth_links" not in counts2,
              f"P3: removing the optional line changed the verdict: {why!r} -> {why2!r}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if fails:
        print("FAIL\n  " + "\n  ".join(fails))
        return 1
    print("PASS: wrong-depth ../ links are reported even when a colliding basename "
          "(start-here, index, an asset) rescues them; right-depth, real cross-vault, "
          "backticked and plain vault-relative links are not; a directory does not count as existing; "
          "BROKEN/CROSS-VAULT/ORPHANS/ALL FILES/GHOST unchanged; --summary keeps the "
          "count; preflight reads it as optional")
    return 0


if __name__ == "__main__":
    sys.exit(main())
