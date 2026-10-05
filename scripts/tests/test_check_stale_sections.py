"""Regression test for the three check_stale.py additions of 2026-09-18.

One throwaway fixture vault exercises all three, because all three live in the
same script and share its line filters and its --summary contract.

  regression R57 -- section B3, terminal `*(unverifiable: <slug>)*` validation
    U1  a valid slug is COUNTED, never flagged
    U2  an invented slug is INVALID, with the right file:line
    U3  the colon-less near-miss `*(unverifiable)*` is INVALID, not ignored
    U4  a `<placeholder>` reason is not a violation ON A CONTENT PAGE
        (the file-skip list alone would not cover this)
    U5  a label quoted in wiki/log.md is not a live label
    U6  a label inside a ``` fence is not a live label
    U7  ~~struck~~ and [x] lines are exempt, as in section B
    U8  an invalid slug on a TRACKER page is still flagged (unlike a marker,
        a bad slug is not an "expected open item")
    U9  the synced convention doc may spell the form out freely
    U10 the label still trips NO section-B marker -- the regression R46 guarantee
    U11 exit code stays 0 with invalid labels present (advisory contract)
    U12 the REAL synced _meta/fleet-conventions.md is covered by the skip list
        (it now carries would-be-INVALID strings; nothing else protects them)
    U13 PROSE using the word in brackets is not a label and is never flagged
    U14 the italics-forgotten form `(unverifiable: slug)` IS still validated
    U15 a label quoted in `inline code` is documentation, like one in a fence
    U16 a `changelog.md` content page is NOT exempt (basename, not endswith)

  regression R66 -- section E, required instance-owned scaffold
    S1  a complete vault reports "7 required / 0 missing" and no detail lines
    S2  gaps print `MISSING SCAFFOLD : <path>`, in REQUIRED_SCAFFOLD order
    S3  exit code stays 0 with gaps present
    S4  the token is NOT sync_from_template's bare "MISSING : "

  regression R74 -- section A, the marker-vs-prose hint
    L1  an overdue loop closed only in prose -> the hint prints exactly once
    L2  all loops [x] -> 0 open / 0 overdue and NO hint
    L3  open but not yet overdue -> NO hint (it would be noise every session)
    L4  --summary prints the hint, the INVALID lines and the scaffold gaps

  Shared
    P1  maintenance_preflight.parse_stale still parses the whole report --
        sections B3 and E must not disturb its A/B/B2/C/D regexes

Run:  python scripts/tests/test_check_stale_sections.py

Exit 0 = all assertions pass.  Exit 1 = at least one red.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_SCRIPTS = Path(__file__).resolve().parents[1]

FENCE = "```"

CLAIMS = f"""---
title: Claims
description: content page carrying terminal labels
---

# Claims

The 1993 dose figure is *(unverifiable: source-gone)* and stays as written.
The broadcast date is *(unverifiable: could-not-find)*, which is not a slug.
A near miss with no reason at all: *(unverifiable)* -- still a live label.
The convention spells the form as *(unverifiable: <slug>)* in prose.
The 1975 attendance figure is disputed (unverifiable without the register).
An italics-forgotten label: (unverifiable: italics-forgotten) stays validated.
Documented inline as `*(unverifiable: backticked-slug)*` -- quoted, not applied.
~~The retracted figure was *(unverifiable: struck-slug)*~~ -- superseded.

- [x] retired claim *(unverifiable: checked-slug)* -- resolved, kept for the record

{FENCE}
*(unverifiable: fenced-slug)*
{FENCE}
"""

TERMINAL_ONLY = """---
title: Terminal only
description: its ONLY flippable-looking content is a terminal label
---

# Terminal only

The attendance figure is *(unverifiable: paywalled)* and will stay that way.
"""

TRACKER = """---
title: Open questions
description: a declared tracker page
tracker_page: true
---

# Open questions

- the 1993 figure, provisionally *(unverifiable: tracker-slug)* -- chase it
"""

CHANGELOG = """---
title: Changelog
description: an ordinary content page whose FILENAME merely ends in log.md
---

# Changelog

2026-09-18: the dose claim was downgraded to *(unverifiable: changelog-slug)*.
"""

LOG = """# Log

## [2026-09-18] meta | terminal-label downgrade

Downgraded the dose claim to *(unverifiable: logged-slug)* after the archive
search came back empty. Quoted here for the record, not applied here.
"""

FLEET_CONVENTIONS = """---
title: Fleet conventions
description: synced convention doc -- spells the form out by definition
---

# Fleet conventions

The terminal label is `*(unverifiable: <reason>)*` over a closed vocabulary.
A counter-example such as *(unverifiable: conventions-slug)* is documentation,
not a live label, and this file is skipped for exactly that reason.
"""

LEDGER_OVERDUE = f"""---
title: Open loops
description: deferred-propagation ledger
---

# Open loops

## Format

{FENCE}
- [ ] <specific task> -- trigger: YYYY-MM-DD
{FENCE}

## Open

- [ ] re-key the attendance figure across the concept pages -- trigger: 2020-01-01 (opened 2019-12-01)
  CLOSED 2026-09-18: done this session, evidence in log.md
- [~] sweep the terminology pass -- trigger: 2020-02-01 (opened 2019-12-01)
  RESOLVED 2026-09-18: finished, see log.md
"""

LEDGER_CLOSED = LEDGER_OVERDUE.replace("- [ ] re-key", "- [x] re-key").replace(
    "- [~] sweep", "- [x] sweep")
LEDGER_FUTURE = LEDGER_OVERDUE.replace("trigger: 2020-01-01", "trigger: 2099-01-01").replace(
    "trigger: 2020-02-01", "trigger: 2099-02-01")

HINT = "a loop is closed by flipping its marker to [x]"
SECTION_A_RE = re.compile(r"^=== A\.[^\n]*=== (\d+) open / (\d+) overdue", re.M)
B3_RE = re.compile(r"^=== B3\.[^\n]*=== (\d+) valid / (\d+) INVALID", re.M)
E_RE = re.compile(r"^=== E\.[^\n]*=== (\d+) required / (\d+) missing", re.M)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def lineno_of(text: str, needle: str) -> int:
    """1-based line number of the first line containing `needle` (0 = absent)."""
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 0


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / "check_stale.py"), *args],
        capture_output=True, cwd=str(root), env=env,
    )
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    proc.stderr = proc.stderr.decode("utf-8", "replace")
    return proc


def build(root: Path, scripts_from: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(scripts_from / "check_stale.py", root / "scripts" / "check_stale.py")
    (root / "scripts" / "README.md").write_text("# scripts\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Fixture wiki\n", encoding="utf-8")
    meta = root / "_meta"
    meta.mkdir()
    (meta / "llm-wiki.md").write_text("# The pattern\n", encoding="utf-8")
    (meta / "obsidian-syntax.md").write_text("# Syntax\n", encoding="utf-8")
    (meta / "open-loops.md").write_text(LEDGER_OVERDUE, encoding="utf-8")
    (meta / "fleet-conventions.md").write_text(FLEET_CONVENTIONS, encoding="utf-8")
    wiki = root / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "analysis").mkdir()
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "log.md").write_text(LOG, encoding="utf-8")
    (wiki / "concepts" / "claims.md").write_text(CLAIMS, encoding="utf-8")
    (wiki / "concepts" / "terminal-only.md").write_text(TERMINAL_ONLY, encoding="utf-8")
    (wiki / "concepts" / "changelog.md").write_text(CHANGELOG, encoding="utf-8")
    (wiki / "analysis" / "open-questions.md").write_text(TRACKER, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-from", default=str(DEFAULT_SCRIPTS))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    scripts_from = Path(args.scripts_from)
    tmp = Path(tempfile.mkdtemp(prefix="wikillm-check-stale-"))
    root = tmp / "vault"
    try:
        build(root, scripts_from)
        print(f"fixture: {root}\nscript under test: {scripts_from}\n")

        proc = run(root)
        out = proc.stdout
        print(out)
        print("---- assertions ----")

        # ---------- regression R57: section B3 ----------
        m = B3_RE.search(out)
        check("U1 valid slugs are counted, not flagged",
              bool(m) and m[1] == "2",
              f"header={m.group(0) if m else 'ABSENT'} (source-gone + paywalled)")

        bad_line = lineno_of(CLAIMS, "could-not-find")
        check("U2 invented slug -> INVALID at the right file:line",
              f"wiki/concepts/claims.md:{bad_line}  INVALID: 'could-not-find'" in out,
              f"expected wiki/concepts/claims.md:{bad_line}")

        empty_line = lineno_of(CLAIMS, "A near miss")
        check("U3 colon-less *(unverifiable)* -> INVALID, not ignored",
              f"wiki/concepts/claims.md:{empty_line}  INVALID: '(no reason given)'" in out,
              f"expected wiki/concepts/claims.md:{empty_line}")

        check("U4 <placeholder> reason on a CONTENT page is not a violation",
              "'<slug>'" not in out, "the placeholder rule, not the file skip")

        check("U5 a label quoted in wiki/log.md is not live", "logged-slug" not in out,
              "log.md is an append-only record of past downgrades")

        check("U6 a label inside a fence is not live", "fenced-slug" not in out,
              "same fence filter as section B")

        check("U7 ~~struck~~ and [x] lines are exempt",
              "struck-slug" not in out and "checked-slug" not in out,
              "same resolved-line filters as section B")

        tracker_line = lineno_of(TRACKER, "tracker-slug")
        check("U8 an invalid slug on a TRACKER page is still flagged",
              f"wiki/analysis/open-questions.md:{tracker_line}  INVALID: 'tracker-slug'" in out,
              "a bad slug is not an expected open item")

        check("U9 the synced convention doc may spell the form out",
              "conventions-slug" not in out, "_meta/fleet-conventions.md is skipped")

        section_b = out.split("=== B2.", 1)[0]
        check("U10 a terminal label trips NO section-B marker (regression R46)",
              "wiki/concepts/terminal-only.md" not in section_b and m is not None and m[2] == "5",
              "the page whose only flippable-looking content is a valid label")

        check("U11 exit 0 with invalid labels present", proc.returncode == 0,
              f"rc={proc.returncode} (advisory: never fails a build)")

        # U12 pins the REAL synced doc, not a hand-written stand-in: this change
        # added two would-be-INVALID strings to it, and only the skip list keeps
        # them out of every vault's close-out.
        sys.path.insert(0, str(scripts_from))
        try:
            import check_stale  # noqa: E402
        finally:
            sys.path.pop(0)
        live_fc = scripts_from.parent / "_meta" / "fleet-conventions.md"
        if live_fc.exists():
            live_hits = check_stale.scan_unverifiable(live_fc.read_text(encoding="utf-8"))
            would_be = [h[1] or "(no reason given)" for h in live_hits if not h[2]]
            check("U12 the live _meta/fleet-conventions.md is covered by the skip list",
                  "_meta/fleet-conventions.md" in check_stale.LABEL_SKIP_FILES,
                  f"{len(would_be)} would-be-INVALID hit(s) in the live doc: {would_be}")
        else:
            check("U12 the live _meta/fleet-conventions.md is covered by the skip list",
                  False, f"not found at {live_fc}")

        check("U13 prose using the word in brackets is never flagged",
              "without the register" not in out,
              "B3 validates the label's shape, not the word")

        forgotten_line = lineno_of(CLAIMS, "italics-forgotten label")
        check("U14 the italics-forgotten (unverifiable: slug) form is validated",
              f"wiki/concepts/claims.md:{forgotten_line}  INVALID: 'italics-forgotten'" in out,
              f"expected wiki/concepts/claims.md:{forgotten_line}")

        check("U15 a label in `inline code` is quoted, not applied",
              "backticked-slug" not in out, "same reasoning as the fence filter")

        changelog_line = lineno_of(CHANGELOG, "changelog-slug")
        check("U16 a changelog.md content page is NOT exempt (basename, not endswith)",
              f"wiki/concepts/changelog.md:{changelog_line}  INVALID: 'changelog-slug'" in out,
              "only a file literally named log.md is the append-only record")

        # ---------- regression R66: section E ----------
        me = E_RE.search(out)
        check("S1 complete vault -> 'N required / 0 missing', no detail lines",
              bool(me) and me[1] == "7" and me[2] == "0" and "MISSING SCAFFOLD" not in out,
              f"header={me.group(0) if me else 'ABSENT'}")

        (root / "CLAUDE.md").unlink()
        (root / "_meta" / "llm-wiki.md").unlink()
        proc_gaps = run(root)
        gaps_out = proc_gaps.stdout
        me = E_RE.search(gaps_out)
        i_claude = gaps_out.find("MISSING SCAFFOLD : CLAUDE.md")
        i_llm = gaps_out.find("MISSING SCAFFOLD : _meta/llm-wiki.md")
        check("S2 gaps print MISSING SCAFFOLD, in REQUIRED_SCAFFOLD order",
              bool(me) and me[2] == "2" and i_claude != -1 and i_llm != -1 and i_claude < i_llm,
              f"header={me.group(0) if me else 'ABSENT'} claude@{i_claude} llm@{i_llm}")

        check("S3 exit 0 with scaffold gaps present", proc_gaps.returncode == 0,
              f"rc={proc_gaps.returncode}")

        check("S4 token is distinct from sync_from_template's 'MISSING : '",
              re.search(r"^MISSING : ", gaps_out, re.M) is None,
              "a close-out log must not conflate scaffold gaps with sync gaps")

        (root / "CLAUDE.md").write_text("# Fixture wiki\n", encoding="utf-8")
        (root / "_meta" / "llm-wiki.md").write_text("# The pattern\n", encoding="utf-8")

        # ---------- regression R74: section A hint ----------
        ma = SECTION_A_RE.search(out)
        check("L1 overdue loop closed only in prose -> hint, exactly once",
              bool(ma) and ma[1] == "2" and ma[2] == "2" and out.count(HINT) == 1,
              f"header={ma.group(0) if ma else 'ABSENT'} hint_count={out.count(HINT)}")

        (root / "_meta" / "open-loops.md").write_text(LEDGER_CLOSED, encoding="utf-8")
        closed_out = run(root).stdout
        ma = SECTION_A_RE.search(closed_out)
        check("L2 markers flipped to [x] -> 0 open / 0 overdue, no hint",
              bool(ma) and ma[1] == "0" and ma[2] == "0" and HINT not in closed_out,
              f"header={ma.group(0) if ma else 'ABSENT'}")

        (root / "_meta" / "open-loops.md").write_text(LEDGER_FUTURE, encoding="utf-8")
        future_out = run(root).stdout
        ma = SECTION_A_RE.search(future_out)
        check("L3 open but not overdue -> no hint (it would be noise)",
              bool(ma) and ma[1] == "2" and ma[2] == "0" and HINT not in future_out,
              f"header={ma.group(0) if ma else 'ABSENT'}")

        (root / "_meta" / "open-loops.md").write_text(LEDGER_OVERDUE, encoding="utf-8")
        (root / "CLAUDE.md").unlink()
        summary_out = run(root, "--summary").stdout
        check("L4 --summary keeps hint, INVALID lines and scaffold gaps",
              HINT in summary_out and "INVALID: 'could-not-find'" in summary_out
              and "MISSING SCAFFOLD : CLAUDE.md" in summary_out,
              "all three are actionable, like OVERDUE and DUE")
        (root / "CLAUDE.md").write_text("# Fixture wiki\n", encoding="utf-8")

        # ---------- shared: the downstream parser ----------
        sys.path.insert(0, str(scripts_from))
        try:
            import maintenance_preflight  # noqa: E402
            counts, err = maintenance_preflight.parse_stale(out)
        finally:
            sys.path.pop(0)
        check("P1 maintenance_preflight.parse_stale still parses the report",
              err is None and counts.get("open_loops_overdue") == 2
              and counts.get("markers_tracker_hits") is not None
              and counts.get("recurring_tracked") is not None,
              f"err={err} counts={sorted(counts)}")

        print()
        failed = [n for n, ok, _ in results if not ok]
        if failed:
            print(f"RED -- {len(failed)}/{len(results)} assertions failed: {failed}")
            return 1
        print(f"GREEN -- all {len(results)} assertions passed")
        return 0
    finally:
        if args.keep:
            print(f"\n(fixture kept at {root})")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
