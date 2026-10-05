#!/usr/bin/env python3
"""Regression test for regression R97 -- audit_claims.py --include forces EVERY
matching population entry and rejects malformed specs.

The forced-include loop in main() used to `break` on the FIRST population
entry matching a spec, so a claim line citing two sources (or the same
source twice) was force-included once instead of once per wikilink match --
the queue contract everywhere else (and what the claim-audit workflow
counts). A spec with no colon (`positions-register.md`) or no path (`:42`)
left the path part EMPTY, and `endswith("")` matched every page, so the
first population entry -- whatever it was -- got forced.

Cases (fixture: one page, line 7 cites alpha+beta, line 8 cites alpha
twice, line 9 cites beta once -> population 5):
  I1  --include page.md:7  with --n 0 -> exactly 2 queue entries for line 7
      (RED control: the old `break` produced 1; assert == 2, not >= 1)
  I2  --include page.md:8  -> 2 entries, same slug twice (same-source line)
  I3  --include page.md:   (FILE:, no line) -> all 5 entries in the file
  I4  --include positions-register.md (no colon) -> exit 2, message names
      the spec, NO queue printed (nothing forced, nothing sampled)
  I5  --include :42 -> exit 2, same
  I6  --include page.md:abc (non-numeric line) -> exit 2
  I7  dedupe is per ENTRY against the sample: --n 5 (everything sampled)
      + --include page.md:7 prints n=5, not 7
  I8  overlapping specs (page.md:7 and page.md:) force each entry once: n=5
  I9  a spec matching nothing warns on stderr, exits 0, forces nothing
  I10 forced entries still bypass the judged-claims ledger skip: a ledger
      row FAITHFUL this quarter for line 9 + --include page.md:9 -> the
      entry is queued anyway
  I11 the rejection happens BEFORE the ledger is read: a corrupt ledger plus
      a malformed spec exits 2 with the spec message, not the ledger warning
  I12 multiset dedupe: with --n 1 and a seed under which the sampler holds ONE
      of line 8's two identical entries, --include page.md:8 still yields two
      entries for line 8 (n=2) -- the identity dedupe must count, not collapse
  I13 the path part matches on a `/` boundary: --include e.md: (a bare string
      suffix of page.md) matches nothing and forces nothing

Builds a throwaway vault and runs the script UNDER TEST via --root.
Run: python scripts/tests/test_include_spec.py    Exit 0 = pass.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
AUDIT = SCRIPTS / "audit_claims.py"
PAGE_REL = "wiki/concepts/page.md"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def build(root: Path) -> None:
    (root / "_meta").mkdir(parents=True)
    (root / "raw").mkdir()
    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    for slug in ("alpha", "beta"):
        (src / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\n---\n\n# {slug}\n\n## Section\n\nbody\n", encoding="utf-8")
    (root / "wiki" / "concepts").mkdir()
    (root / "wiki" / "concepts" / "page.md").write_text(
        "---\ntitle: Page\n---\n\n# Page\n\n"
        "- INCL7 two-source claim per [[sources/alpha]] and [[sources/beta]].\n"
        "- INCL8 same-source twice: [[sources/alpha]] then [[sources/alpha]].\n"
        "- INCL9 single claim per [[sources/beta]].\n",
        encoding="utf-8")


def run(root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(AUDIT), "--root", str(root), "--no-history", *extra],
                          capture_output=True, text=True, errors="replace", cwd=str(SCRIPTS))


def queue_lines(out: str, lineno: int) -> list[str]:
    """The queue entry lines for page.md:<lineno> (path printed as the OS writes it)."""
    pat = re.compile(r"^\s+wiki[\\/]concepts[\\/]page\.md:%d  \[" % lineno)
    return [ln for ln in out.splitlines() if pat.match(ln)]


def printed_n(out: str):
    m = re.search(r"=== CLAIM-AUDIT SAMPLE ===  seed=\S+  n=(\d+)", out)
    return int(m.group(1)) if m else None


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm97-"))
    try:
        vault = tmp / "vault"
        build(vault)

        # I1 -- two-citation line: both entries forced (RED control: old break -> 1)
        r = run(vault, "--n", "0", "--include", f"{PAGE_REL}:7")
        check(r.returncode == 0, f"I1: exit {r.returncode}\n{r.stdout}{r.stderr}")
        q7 = queue_lines(r.stdout, 7)
        check(len(q7) == 2, f"I1: {len(q7)} queue entries for line 7, want exactly 2\n{r.stdout}")
        check(printed_n(r.stdout) == 2, f"I1: printed n={printed_n(r.stdout)}, want 2\n{r.stdout}")
        check("[[sources/alpha]]" in r.stdout and "[[sources/beta]]" in r.stdout,
              f"I1: both source headings expected\n{r.stdout}")
        check(not queue_lines(r.stdout, 8) and not queue_lines(r.stdout, 9),
              f"I1: other lines leaked into the queue\n{r.stdout}")

        # I2 -- same source cited twice on one line: two entries
        r = run(vault, "--n", "0", "--include", f"{PAGE_REL}:8")
        check(r.returncode == 0, f"I2: exit {r.returncode}\n{r.stderr}")
        q8 = queue_lines(r.stdout, 8)
        check(len(q8) == 2, f"I2: {len(q8)} entries for the same-source-twice line, want 2\n{r.stdout}")
        check(printed_n(r.stdout) == 2, f"I2: printed n={printed_n(r.stdout)}\n{r.stdout}")
        check("[[sources/beta]]" not in r.stdout, f"I2: beta must not appear\n{r.stdout}")

        # I3 -- FILE: (no line) forces every claim line in the file
        r = run(vault, "--n", "0", "--include", f"{PAGE_REL}:")
        check(r.returncode == 0, f"I3: exit {r.returncode}\n{r.stderr}")
        check(printed_n(r.stdout) == 5, f"I3: printed n={printed_n(r.stdout)}, want 5\n{r.stdout}")
        check([len(queue_lines(r.stdout, k)) for k in (7, 8, 9)] == [2, 2, 1],
              f"I3: per-line entry counts wrong\n{r.stdout}")

        # I4 / I5 / I6 -- malformed specs exit 2 before anything is printed
        for label, spec in (("I4", "positions-register.md"), ("I5", ":42"), ("I6", f"{PAGE_REL}:abc")):
            r = run(vault, "--n", "0", "--include", spec)
            check(r.returncode == 2, f"{label}: exit {r.returncode} for {spec!r}, want 2\n{r.stdout}{r.stderr}")
            check(spec in r.stderr and "--include" in r.stderr,
                  f"{label}: stderr does not name the spec {spec!r}: {r.stderr!r}")
            check("CLAIM-AUDIT SAMPLE" not in r.stdout and "VERIFICATION QUEUE" not in r.stdout,
                  f"{label}: a rejected spec still printed a queue\n{r.stdout}")

        # I7 -- dedupe against the sample per entry: nothing forced twice
        r = run(vault, "--n", "5", "--include", f"{PAGE_REL}:7")
        check(r.returncode == 0, f"I7: exit {r.returncode}\n{r.stderr}")
        check(printed_n(r.stdout) == 5, f"I7: printed n={printed_n(r.stdout)}, want 5 (no duplicates)\n{r.stdout}")
        check(len(queue_lines(r.stdout, 7)) == 2, f"I7: line 7 must appear exactly twice\n{r.stdout}")

        # I8 -- overlapping specs force each population entry once
        r = run(vault, "--n", "0", "--include", f"{PAGE_REL}:7", "--include", f"{PAGE_REL}:")
        check(r.returncode == 0, f"I8: exit {r.returncode}\n{r.stderr}")
        check(printed_n(r.stdout) == 5, f"I8: printed n={printed_n(r.stdout)}, want 5\n{r.stdout}")
        check(len(queue_lines(r.stdout, 7)) == 2, f"I8: line 7 entries != 2\n{r.stdout}")

        # I9 -- a well-formed spec matching nothing: warn, exit 0, force nothing
        r = run(vault, "--n", "0", "--include", "wiki/concepts/nope.md:3")
        check(r.returncode == 0, f"I9: exit {r.returncode}\n{r.stderr}")
        check("WARNING" in r.stderr and "nope.md:3" in r.stderr, f"I9: no warning on stderr: {r.stderr!r}")
        check(printed_n(r.stdout) == 0, f"I9: printed n={printed_n(r.stdout)}, want 0\n{r.stdout}")

        # I10 -- forced entries bypass a current-quarter FAITHFUL ledger skip
        sys.path.insert(0, str(SCRIPTS))
        sys.dont_write_bytecode = True
        import audit_claims as ac  # noqa: E402
        line9 = "- INCL9 single claim per [[sources/beta]]."
        key9 = ac.claim_key(PAGE_REL, line9)
        today = datetime.date.today()
        period = ac.period_of(today)
        ledger = {"runs": [], "claims": {key9: {
            "relpath": PAGE_REL, "verdict": "FAITHFUL",
            "sources": {"beta": {"verdict": "FAITHFUL", "last_judged_run": "t.1",
                                 "last_judged_period": period}}}}}
        led = vault / "_meta" / "claim-audit-ledger.json"
        led.write_text(json.dumps(ledger), encoding="utf-8")
        r_skip = run(vault, "--n", "5")
        check(len(queue_lines(r_skip.stdout, 9)) == 0,
              f"I10 setup: the ledger skip did not remove line 9 (ledger shape stale?)\n{r_skip.stdout}{r_skip.stderr}")
        r = run(vault, "--n", "0", "--include", f"{PAGE_REL}:9")
        check(r.returncode == 0, f"I10: exit {r.returncode}\n{r.stderr}")
        check(len(queue_lines(r.stdout, 9)) == 1,
              f"I10: forced entry did not bypass the ledger skip\n{r.stdout}{r.stderr}")

        # I11 -- malformed spec is rejected BEFORE the ledger is read
        led.write_text("{not json", encoding="utf-8")
        r = run(vault, "--n", "0", "--include", ":42")
        check(r.returncode == 2, f"I11: exit {r.returncode}\n{r.stdout}{r.stderr}")
        check("':42'" in r.stderr and "ledger" not in r.stderr.lower() and "ledger" not in r.stdout.lower(),
              f"I11: ledger was touched before the spec check\n{r.stdout}{r.stderr}")
        led.unlink()

        # I12 -- multiset dedupe: find a seed under which --n 1 samples one of
        # line 8's two identical entries, then force line 8 and expect BOTH
        found = None
        for seed in ("a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"):
            r1 = run(vault, "--n", "1", "--seed", seed)
            if r1.returncode == 0 and len(queue_lines(r1.stdout, 8)) == 1:
                found = seed
                break
        check(found is not None, "I12 setup: no seed in a..l sampled line 8 alone at --n 1")
        if found is not None:
            r = run(vault, "--n", "1", "--seed", found, "--include", f"{PAGE_REL}:8")
            check(r.returncode == 0, f"I12: exit {r.returncode}\n{r.stderr}")
            check(len(queue_lines(r.stdout, 8)) == 2,
                  f"I12 (seed {found}): {len(queue_lines(r.stdout, 8))} entries for line 8, want 2\n{r.stdout}")
            check(printed_n(r.stdout) == 2, f"I12: printed n={printed_n(r.stdout)}, want 2\n{r.stdout}")

        # I13 -- path part matches on a / boundary, not as a bare string suffix
        r = run(vault, "--n", "0", "--include", "e.md:")
        check(r.returncode == 0, f"I13: exit {r.returncode}\n{r.stderr}")
        check(printed_n(r.stdout) == 0 and "WARNING" in r.stderr,
              f"I13: bare suffix 'e.md:' forced n={printed_n(r.stdout)}, want 0 + warning\n{r.stdout}{r.stderr}")
        r = run(vault, "--n", "0", "--include", "page.md:")
        check(printed_n(r.stdout) == 5, f"I13: basename 'page.md:' should still match (n={printed_n(r.stdout)})\n{r.stdout}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS test_include_spec.py: I1-I13 -- --include forces every matching entry "
          "(2 for a two-citation line, 2 for a same-source-twice line, 5 for FILE:), "
          "dedupes per entry as a multiset, matches the path on a / boundary, warns on "
          "no match, keeps the ledger bypass, and rejects "
          "no-colon / empty-path / non-numeric specs with exit 2 before any ledger read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
