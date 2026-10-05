"""Regression test for check_stale.py section B3's downgrade-log cross-check
(regression R99).

The convention's downgrade rule (item 3, _meta/fleet-conventions.md, marker
hygiene) requires every applied `*(unverifiable: <slug>)*` label to have a
wiki/log.md entry under the heading
`## [YYYY-MM-DD] meta | downgrade | <wiki-relative page path> | <slug>`.
R46 enforced the slug vocabulary; nothing enforced the entry until R99.

  D1  two valid labels, one logged, one not -> `2 valid / 0 INVALID / 1 UNLOGGED`
  D2  the UNLOGGED line names the right file:line and slug, with the fix hint
  D3  the logged label is NOT reported (its entry omits `wiki/` and `.md`:
      the path match is normalized, not literal)
  D4  downgrade_log_entries() on LOG returns EXACTLY the three genuine
      entries: the placeholder-date line, both `<...>`-field lines, a real
      heading inside a ``` fence, one inside a ~~~ fence, and a heading with
      trailing prose after the slug are all excluded; `Meta | Downgrade` in
      capitals is included (case-insensitive)
  D5  an entry with the right page but a DIFFERENT slug is parsed as an entry
      yet does not log the label
  D6  matching is by page + slug, never line numbers: inserting a line above
      the logged label keeps it logged
  D7  the `wiki/` + `.md` spelling of the path also logs it
  D8  --summary prints the UNLOGGED line and the header carries the count
  D9  exit code stays 0 with an UNLOGGED label present (hygiene, not validity)
  P1  maintenance_preflight.parse_stale still parses the report, and the
      older `N valid / M INVALID` prefix regex still matches the new header
  R1  RED CONTROL: a copy with the cross-check stripped reports both labels
      valid and no UNLOGGED token -- the pre-R99 behaviour this test pins away

Run:  python scripts/tests/test_downgrade_log.py

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

sys.dont_write_bytecode = True  # in-process imports below; keep scripts/ clean

DEFAULT_SCRIPTS = Path(__file__).resolve().parents[1]

FENCE = "```"

ALPHA = """---
title: Alpha
description: content page whose downgrade IS logged
---

# Alpha

The 1993 dose figure is *(unverifiable: source-gone)* and stays as written.
"""

BETA = """---
title: Beta
description: content page whose downgrade is NOT logged
---

# Beta

The broadcast date is *(unverifiable: paywalled)* -- nobody wrote the entry.
"""

# Everything a real log.md accumulates around the one genuine entry: a sync
# note quoting the convention (the sample-vault-b false positive), a fenced copy of
# a real-looking heading, and a wrong-slug entry for beta.
LOG = f"""# Log

## [2026-09-20] meta | fleet sync

Adopted the marker-hygiene convention. The downgrade entry shape is
`## [YYYY-MM-DD] meta | downgrade | <wiki-relative page path> | <slug>`.
## [YYYY-MM-DD] meta | downgrade | <wiki-relative page path> | <slug>
## [2026-09-20] meta | downgrade | <wiki-relative page path> | paywalled
## [2026-09-20] meta | downgrade | concepts/beta | <slug>

{FENCE}
## [2026-09-20] meta | downgrade | concepts/beta | paywalled
{FENCE}

~~~
## [2026-09-20] meta | downgrade | concepts/beta | paywalled
~~~

## [2026-09-20] meta | downgrade | concepts/beta | paywalled -- trailing prose

## [2026-09-23] Meta | Downgrade | concepts/gamma | Provenance-Lost

## [2026-09-21] meta | downgrade | concepts/beta | source-gone

Wrong slug on purpose: beta carries `paywalled`, so this must NOT log it.

## [2026-09-22] meta | downgrade | concepts/alpha | source-gone

The 1993 dose figure: the cited archive was deleted 2026-09-01 and no copy
exists in the Wayback Machine (checked 2026-09-22); corroboration attempted
via the 1994 review, which cites the same dead page.
"""

LOG_WIKI_MD = LOG.replace("| concepts/alpha | source-gone", "| wiki/concepts/alpha.md | source-gone")

B3_PREFIX_RE = re.compile(r"^=== B3\.[^\n]*=== (\d+) valid / (\d+) INVALID", re.M)
B3_FULL_RE = re.compile(r"^=== B3\.[^\n]*=== (\d+) valid / (\d+) INVALID / (\d+) UNLOGGED", re.M)

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}\n         {detail}")


def lineno_of(text: str, needle: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return i
    return 0


def run(root: Path, *args: str, script: str = "check_stale.py") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-B", str(root / "scripts" / script), *args],
        capture_output=True, cwd=str(root), env=env,
    )
    proc.stdout = proc.stdout.decode("utf-8", "replace")
    proc.stderr = proc.stderr.decode("utf-8", "replace")
    return proc


def build(root: Path, scripts_from: Path) -> None:
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(scripts_from / "check_stale.py", root / "scripts" / "check_stale.py")
    # RED CONTROL: the same script with the cross-check removed -- every label
    # reads as logged, exactly the pre-R99 state.
    src = (scripts_from / "check_stale.py").read_text(encoding="utf-8")
    needle = "unlogged = unlogged_labels(labeled, read(DOWNGRADE_LOG_PATH))"
    assert needle in src, "red-control needle not found in check_stale.py"
    stripped = src.replace(needle, "unlogged = []")
    (root / "scripts" / "check_stale_nocheck.py").write_text(stripped, encoding="utf-8")
    (root / "scripts" / "README.md").write_text("# scripts\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Fixture wiki\n", encoding="utf-8")
    meta = root / "_meta"
    meta.mkdir()
    (meta / "llm-wiki.md").write_text("# The pattern\n", encoding="utf-8")
    (meta / "obsidian-syntax.md").write_text("# Syntax\n", encoding="utf-8")
    (meta / "open-loops.md").write_text("# Open loops\n", encoding="utf-8")
    wiki = root / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "index.md").write_text("# Index\n", encoding="utf-8")
    (wiki / "log.md").write_text(LOG, encoding="utf-8")
    (wiki / "concepts" / "alpha.md").write_text(ALPHA, encoding="utf-8")
    (wiki / "concepts" / "beta.md").write_text(BETA, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scripts-from", default=str(DEFAULT_SCRIPTS))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    scripts_from = Path(args.scripts_from)
    tmp = Path(tempfile.mkdtemp(prefix="wikillm-downgrade-log-"))
    root = tmp / "vault"
    try:
        build(root, scripts_from)
        print(f"fixture: {root}\nscript under test: {scripts_from}\n")

        proc = run(root)
        out = proc.stdout
        print(out)
        print("---- assertions ----")

        m = B3_FULL_RE.search(out)
        check("D1 header reads 2 valid / 0 INVALID / 1 UNLOGGED",
              bool(m) and m.groups() == ("2", "0", "1"),
              f"header={m.group(0) if m else 'ABSENT'}")

        beta_line = lineno_of(BETA, "paywalled")
        want = f"wiki/concepts/beta.md:{beta_line}  UNLOGGED: 'paywalled' -- no `meta | downgrade` entry in wiki/log.md"
        check("D2 UNLOGGED names the right file:line + slug, with the hint",
              want in out and "meta | downgrade | <page path> | <slug>" in out,
              f"expected line: {want}")

        check("D3 the logged label (entry without wiki/ or .md) is not reported",
              "wiki/concepts/alpha.md" not in out.split("=== C.", 1)[0],
              "path match is normalized: concepts/alpha logs wiki/concepts/alpha.md")

        sys.path.insert(0, str(scripts_from))
        try:
            import check_stale  # noqa: E402
            entries = check_stale.downgrade_log_entries(LOG)
        finally:
            sys.path.pop(0)
        want_entries = {("concepts/beta", "source-gone"), ("concepts/alpha", "source-gone"),
                        ("concepts/gamma", "provenance-lost")}
        check("D4 a note quoting the convention is never an entry (exact entry set)",
              entries == want_entries,
              f"entries={sorted(entries)}")

        check("D5 a right-page WRONG-slug entry is an entry but does not log the label",
              ("concepts/beta", "source-gone") in entries and bool(m) and m[3] == "1",
              "beta has a source-gone entry but carries paywalled")

        shifted = ALPHA.replace("# Alpha\n", "# Alpha\n\nAn inserted line moves the label down.\n")
        (root / "wiki" / "concepts" / "alpha.md").write_text(shifted, encoding="utf-8")
        out_shift = run(root).stdout
        ms = B3_FULL_RE.search(out_shift)
        check("D6 a line shift keeps the label logged (page + slug, not line)",
              bool(ms) and ms[3] == "1" and "wiki/concepts/alpha.md" not in out_shift.split("=== C.", 1)[0],
              f"header={ms.group(0) if ms else 'ABSENT'}")
        (root / "wiki" / "concepts" / "alpha.md").write_text(ALPHA, encoding="utf-8")

        (root / "wiki" / "log.md").write_text(LOG_WIKI_MD, encoding="utf-8")
        out_wm = run(root).stdout
        mw = B3_FULL_RE.search(out_wm)
        check("D7 the wiki/...md spelling of the path also logs it",
              bool(mw) and mw[3] == "1" and "wiki/concepts/alpha.md" not in out_wm.split("=== C.", 1)[0],
              f"header={mw.group(0) if mw else 'ABSENT'}")
        (root / "wiki" / "log.md").write_text(LOG, encoding="utf-8")

        summary = run(root, "--summary").stdout
        msu = B3_FULL_RE.search(summary)
        check("D8 --summary prints the UNLOGGED line and count",
              want in summary and bool(msu) and msu[3] == "1",
              f"header={msu.group(0) if msu else 'ABSENT'}")

        check("D9 exit 0 with an UNLOGGED label present", proc.returncode == 0,
              f"rc={proc.returncode} (advisory: hygiene never fails a build)")

        sys.path.insert(0, str(scripts_from))
        try:
            import maintenance_preflight  # noqa: E402
            counts, err = maintenance_preflight.parse_stale(out)
        finally:
            sys.path.pop(0)
        mp = B3_PREFIX_RE.search(out)
        check("P1 parse_stale still parses; the older B3 prefix regex still matches",
              err is None and counts.get("markers_tracker_hits") is not None
              and bool(mp) and mp.groups() == ("2", "0"),
              f"err={err} prefix={mp.group(0) if mp else 'ABSENT'}")

        red = run(root, script="check_stale_nocheck.py")
        mr = B3_FULL_RE.search(red.stdout)
        check("R1 RED CONTROL: cross-check stripped -> both valid, 0 UNLOGGED, no line",
              red.returncode == 0 and bool(mr) and mr.groups() == ("2", "0", "0")
              and "UNLOGGED:" not in red.stdout,
              f"header={mr.group(0) if mr else 'ABSENT'} (the pre-R99 silence)")

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
