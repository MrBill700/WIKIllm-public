#!/usr/bin/env python3
"""Regression test for regression R100 -- audit_claims.py STRATA telemetry.

The quarterly audit wants to SEE two strata before anyone changes the sampling
weights (ruling 2026-09-27: risk_score and the draw stay as they are): claims
whose cited sources/ page stands on a MACHINE-tier transcript, and claims on
(or citing a sources page carrying) a valid *(unverifiable: <slug>)* label.

Fixture (population 8, one entry per sources wikilink):
  sources/machine.md   transcript_kind: whisper-local          -> machine tier
  sources/legacy.md    transcript_kind: captions (auto)         -> machine via the compat map
  sources/manual.md    transcript_kind: manual-captions         -> NOT machine (verified tier)
  sources/plain.md     no transcript_kind                       -> NOT machine
  sources/labeled.md   valid *(unverifiable: source-gone)*      -> terminal-labeled
  sources/badlabel.md  INVALID *(unverifiable: made-up)*        -> NOT counted
  concepts/page.md     one claim line per sources page above    (6 entries)
  concepts/labeled-page.md  carries a valid label, cites plain  (1 entry, terminal-labeled)
  concepts/quoted.md   label inside `backticks`, cites plain    (1 entry, NOT counted)

Cases:
  S1  --n 8: STRATA block pins machine-tier 2 of 8 / 2 of 8 and terminal-labeled
      2 of 8 / 2 of 8; both lines appear again under NEXT STEPS
  S2  --n 3 at two seeds: the queue ids (and their order) are byte-identical to
      main's pre-change audit_claims.py; RED control: main prints no STRATA line
  S3  --record-run stores an additive `strata` field (population from the vault,
      queued from the rows by key + slug); recording the same run_record again is
      still detected as a duplicate (runs stay 1); the rest of the entry is unchanged
  S4  a copy of the script run WITHOUT its check_stale sibling prints
      `strata unavailable`, exits 0 and serves the identical queue
  S5  the counts are per claim-source ENTRY: a line citing machine twice counts 2
  S6  a sibling check_stale.py missing a helper (version skew mid-sync) prints
      `strata unavailable`, exits 0 and serves the identical queue
  S3b --record-run beside a missing or skewed sibling stores strata={"note": ...}
      (no fabricated zeros) and warns on stdout
  S7  an isolated audit_claims.py + check_stale.py pair writes no __pycache__

S2's baseline is PINNED to c376aaa (main just before this change). A later,
deliberate change to the draw must bump that pin; S4/S6 carry the durable
invariant (the strata code path never alters the queue).

Builds a throwaway vault and runs the script UNDER TEST via --root.
Run: python scripts/tests/test_audit_strata.py    Exit 0 = pass.
"""
from __future__ import annotations

from legacy_fixture import legacy_result
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
AUDIT = SCRIPTS / "audit_claims.py"
fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        fails.append(msg)


def build(root: Path) -> None:
    (root / "_meta").mkdir(parents=True)
    (root / "raw").mkdir()
    src = root / "wiki" / "sources"
    src.mkdir(parents=True)
    pages = {
        "machine": ("transcript_kind: whisper-local\n", ""),
        "legacy": ("transcript_kind: captions (auto)\n", ""),
        "manual": ("transcript_kind: manual-captions\n", ""),
        "plain": ("", ""),
        "labeled": ("", "The figure is gone *(unverifiable: source-gone)* -- see log.\n"),
        "badlabel": ("", "Dubious *(unverifiable: made-up)* label here.\n"),
    }
    for slug, (fm_extra, body) in pages.items():
        (src / f"{slug}.md").write_text(
            f"---\ntitle: {slug}\n{fm_extra}---\n\n# {slug}\n\n## Section\n\n{body}body\n",
            encoding="utf-8")
    con = root / "wiki" / "concepts"
    con.mkdir()
    (con / "page.md").write_text(
        "---\ntitle: Page\n---\n\n# Page\n\n"
        "- CLAIM machine 12 mg per [[sources/machine]].\n"
        "- CLAIM legacy per [[sources/legacy]].\n"
        "- CLAIM manual per [[sources/manual#Section]].\n"
        "- CLAIM plain per [[sources/plain]].\n"
        "- CLAIM labeled per [[sources/labeled]].\n"
        "- CLAIM badlabel per [[sources/badlabel]].\n",
        encoding="utf-8")
    (con / "labeled-page.md").write_text(
        "---\ntitle: Labeled\n---\n\n# Labeled\n\n"
        "- The old number *(unverifiable: paywalled)* stays as prose.\n"
        "- CLAIM lp per [[sources/plain]].\n",
        encoding="utf-8")
    (con / "quoted.md").write_text(
        "---\ntitle: Quoted\n---\n\n# Quoted\n\n"
        "- The convention writes `*(unverifiable: source-gone)*`; not applied here.\n"
        "- CLAIM q per [[sources/plain]].\n",
        encoding="utf-8")


def run(script: Path, root: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), "--root", str(root), "--no-history", *extra],
                          capture_output=True, text=True, errors="replace", cwd=str(script.parent))


def queue_entries(out: str) -> list[tuple[str, str]]:
    """[(id, slug), ...] in printed order."""
    entries, slug = [], None
    for ln in out.splitlines():
        m = re.match(r"^\s+\[\[sources/([^\]]+)\]\]", ln)
        if m:
            slug = m.group(1)
            continue
        m = re.search(r"\bid=([0-9a-f]{16})\s*$", ln)
        if m and slug:
            entries.append((m.group(1), slug))
    return entries


def strata_lines(out: str) -> list[str]:
    return [ln.strip() for ln in out.splitlines()
            if re.match(r"^\s*(machine-tier|terminal-labeled): \d+ of \d+ population / \d+ of \d+ queued$", ln)]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="wikillm100-"))
    pinned: list[str] = []
    try:
        vault = tmp / "vault"
        build(vault)

        # S1 -- counts pinned over population and queue (everything queued)
        r = run(AUDIT, vault, "--n", "8", "--seed", "strata-1")
        check(r.returncode == 0, f"S1: exit {r.returncode}\n{r.stdout}{r.stderr}")
        check("=== STRATA" in r.stdout, f"S1: no STRATA block\n{r.stdout}")
        lines = strata_lines(r.stdout)
        want = ["machine-tier: 2 of 8 population / 2 of 8 queued",
                "terminal-labeled: 2 of 8 population / 2 of 8 queued"]
        check(lines == want + want, f"S1: strata lines {lines}, want {want} in the block AND under NEXT STEPS\n{r.stdout}")
        ns = r.stdout.split("=== NEXT STEPS")[-1] if "=== NEXT STEPS" in r.stdout else ""
        check(all(w in ns for w in want), f"S1: NEXT STEPS lacks the strata lines\n{ns}")
        check("strata unavailable" not in r.stdout, f"S1: strata reported unavailable beside check_stale.py\n{r.stdout}")
        pinned.append(f"S1 {lines[0]}; {lines[1]} (manual-captions, absent kind, INVALID + quoted labels not counted)")

        # S2 -- queue identical to main's script at the same seed; main has no STRATA (RED control)
        main_copy = tmp / "main" / "audit_claims.py"
        main_copy.parent.mkdir()
        git = legacy_result("c376aaa:scripts/audit_claims.py", text=True, errors="replace")
        if git.returncode != 0:
            fails.append(f"S2: could not read the legacy fixture audit_claims.py: {git.stderr}")
        else:
            main_copy.write_text(git.stdout, encoding="utf-8")
            for seed in ("2026-Q3", "drill-7"):
                new = run(AUDIT, vault, "--n", "3", "--seed", seed)
                old = run(main_copy, vault, "--n", "3", "--seed", seed)
                check(new.returncode == 0 and old.returncode == 0, f"S2: exit new={new.returncode} old={old.returncode}\n{new.stderr}{old.stderr}")
                qn, qo = queue_entries(new.stdout), queue_entries(old.stdout)
                check(len(qn) == 3, f"S2 seed {seed}: {len(qn)} queue entries, want 3\n{new.stdout}")
                check(qn == qo, f"S2 seed {seed}: queue differs from main's script\n new={qn}\n old={qo}")
                check("STRATA" not in old.stdout and not strata_lines(old.stdout),
                      f"S2 RED control: main's script already prints strata\n{old.stdout}")
                check(strata_lines(new.stdout)[:1] == [f"machine-tier: 2 of 8 population / "
                                                      f"{sum(1 for _, s in qn if s in ('machine', 'legacy'))} of 3 queued"],
                      f"S2 seed {seed}: queued machine count does not match the served queue\n{new.stdout}")
            pinned.append("S2 queue ids identical to main c376aaa at seeds 2026-Q3 + drill-7 (n=3); main prints no STRATA line")

        # S3 -- --record-run stores an additive strata field; duplicate detection survives it
        full = run(AUDIT, vault, "--n", "8", "--seed", "strata-1")
        ids = dict((s, k) for k, s in queue_entries(full.stdout))
        rows = [{"idx": 0, "key": ids["machine"], "file": "wiki/concepts/page.md", "line": 7, "slug": "machine",
                 "refute_status": "arbiter", "final_verdict": "FAITHFUL", "escalation": []},
                {"idx": 1, "key": ids["labeled"], "file": "wiki/concepts/page.md", "line": 11, "slug": "labeled",
                 "refute_status": "arbiter", "final_verdict": "DRIFTED", "escalation": ["scope: DRIFTED"]},
                {"idx": 2, "key": "0000000000000000", "file": "wiki/concepts/page.md", "line": 99, "slug": "plain",
                 "refute_status": "arbiter", "final_verdict": "FAITHFUL", "escalation": []}]
        rec = {"kind": "workflow", "seed": "strata-1", "n": 3, "queued": 3, "printed_n": 3, "hits": 1, "cleared": 0,
               "split": 0, "unconfirmed": 0, "skipped_faithful": 0, "escalated": 1, "borderline": 0, "material": 1,
               "chart_read": 0, "lens_disagreement": 0, "escalation_reasons": {"scope.verdict": 1},
               "spot_check": [], "rows": rows}
        rr = tmp / "rr.json"
        rr.write_text(json.dumps(rec), encoding="utf-8")
        r1 = run(AUDIT, vault, "--record-run", str(rr))
        check(r1.returncode == 0, f"S3: record exit {r1.returncode}\n{r1.stdout}{r1.stderr}")
        ledger_path = vault / "_meta" / "claim-audit-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        runs = ledger.get("runs", [])
        check(len(runs) == 1, f"S3: {len(runs)} runs after one record")
        st = runs[0].get("strata") if runs else None
        want_st = {"machine_tier": {"population": 2, "queued": 1}, "terminal_labeled": {"population": 2, "queued": 1},
                   "population": 8, "queued": 2}
        check(st == want_st, f"S3: strata on the run entry {st}, want {want_st}")
        rest = {k: v for k, v in (runs[0] if runs else {}).items() if k not in ("run", "recorded", "strata")}
        check(rest == rec, "S3: the run entry's other fields changed (strata must be purely additive)")
        r2 = run(AUDIT, vault, "--record-run", str(rr))
        check(r2.returncode == 0 and "already recorded as run" in r2.stdout,
              f"S3: re-recording the same record was not a no-op\n{r2.stdout}{r2.stderr}")
        runs2 = json.loads(ledger_path.read_text(encoding="utf-8")).get("runs", [])
        check(len(runs2) == 1, f"S3 RED: strata on the stored entry broke duplicate detection -- {len(runs2)} runs")
        pinned.append(f"S3 --record-run entry strata={json.dumps(st)}; duplicate re-record still collapses (runs=1)")

        # S4 -- standalone copy (no check_stale.py beside it): strata unavailable, queue unchanged
        alone = tmp / "alone" / "audit_claims.py"
        alone.parent.mkdir()
        shutil.copy2(AUDIT, alone)
        ra = run(alone, vault, "--n", "3", "--seed", "2026-Q3")
        rb = run(AUDIT, vault, "--n", "3", "--seed", "2026-Q3")
        check(ra.returncode == 0, f"S4: standalone exit {ra.returncode}\n{ra.stderr}")
        check("strata unavailable" in ra.stdout and "check_stale.py not importable" in ra.stdout,
              f"S4: standalone copy did not announce the fallback\n{ra.stdout}")
        check(queue_entries(ra.stdout) == queue_entries(rb.stdout), "S4: standalone copy served a different queue")
        check(not list((tmp / "alone").glob("__pycache__")), "S4: bytecode written beside the standalone copy")
        pinned.append("S4 standalone copy: 'strata unavailable: check_stale.py not importable ...', queue identical, exit 0")

        # S5 -- per-entry counting: a line citing machine twice contributes 2
        (vault / "wiki" / "concepts" / "twice.md").write_text(
            "---\ntitle: Twice\n---\n\n# Twice\n\n- CLAIM twice [[sources/machine]] and [[sources/machine]].\n",
            encoding="utf-8")
        # --include-judged: S3 just recorded a FAITHFUL for the machine entry this quarter
        r5 = run(AUDIT, vault, "--n", "10", "--seed", "strata-1", "--include-judged")
        l5 = strata_lines(r5.stdout)
        check(l5[:1] == ["machine-tier: 4 of 10 population / 4 of 10 queued"], f"S5: {l5}\n{r5.stdout}")
        pinned.append(f"S5 {l5[0] if l5 else '(no line)'} after a same-source-twice line")

        # S6 -- version skew: a sibling check_stale.py lacking a helper the strata use
        # (an older vault copy mid-sync) degrades to `strata unavailable`, never an
        # AttributeError that kills the audit before the queue prints.
        skew = tmp / "skew"
        skew.mkdir()
        shutil.copy2(AUDIT, skew / "audit_claims.py")
        (skew / "check_stale.py").write_text(
            'TRANSCRIPT_KINDS = ("auto-captions", "whisper-local", "whisper-remote")\n'
            "def frontmatter(text):\n    return ''\n",
            encoding="utf-8")
        rs = run(skew / "audit_claims.py", vault, "--n", "3", "--seed", "2026-Q3")
        rt = run(AUDIT, vault, "--n", "3", "--seed", "2026-Q3")
        check(rs.returncode == 0, f"S6: skewed sibling exit {rs.returncode}\n{rs.stderr}")
        check("strata unavailable" in rs.stdout and "lacks" in rs.stdout and "scan_unverifiable" in rs.stdout,
              f"S6: skewed sibling did not name the missing helper\n{rs.stdout}")
        check(queue_entries(rs.stdout) == queue_entries(rt.stdout) and len(queue_entries(rs.stdout)) == 3,
              "S6: skewed sibling served a different queue")
        pinned.append("S6 sibling check_stale.py missing scan_unverifiable/_fm_values/legacy_kind: exit 0, 'strata unavailable', queue identical")

        # S3b -- --record-run beside a missing or skewed sibling stores a NOTE, never
        # fabricated zeros that read as real counts, and says so on stdout.
        for tag, script, needle in (("missing", alone, "not importable"), ("skewed", skew / "audit_claims.py", "lacks")):
            v = tmp / f"vault-3b-{tag}"
            build(v)
            rb3 = run(script, v, "--record-run", str(rr))
            check(rb3.returncode == 0, f"S3b {tag}: record exit {rb3.returncode}\n{rb3.stdout}{rb3.stderr}")
            check("WARNING: strata unavailable" in rb3.stdout and needle in rb3.stdout,
                  f"S3b {tag}: record-run stdout did not warn\n{rb3.stdout}")
            runs3 = json.loads((v / "_meta" / "claim-audit-ledger.json").read_text(encoding="utf-8")).get("runs", [])
            st3 = runs3[0].get("strata") if runs3 else None
            check(isinstance(st3, dict) and set(st3) == {"note"} and needle in str(st3.get("note")),
                  f"S3b {tag}: run entry strata {st3}, want {{'note': ...}} with no counts")
            rest3 = {k: val for k, val in (runs3[0] if runs3 else {}).items() if k not in ("run", "recorded", "strata")}
            check(rest3 == rec, f"S3b {tag}: the run entry's other fields changed")
        pinned.append("S3b --record-run beside a missing / skewed check_stale.py: entry strata={'note': ...} (no zeros), stdout WARNING")

        # NIT fix: the unavailable line names the cause once, not 'strata unavailable' twice
        check(all(ln.count("strata unavailable") == 1 for ln in ra.stdout.splitlines() if "strata unavailable" in ln),
              f"S4: 'strata unavailable' repeated within one line\n{ra.stdout}")

        # S7 -- no bytecode: the script + its real sibling run from an isolated copy write
        # no __pycache__ there (the R119 rule; the in-tree scripts/__pycache__ is shared
        # with other suites, so it cannot be the witness).
        pair = tmp / "pair"
        pair.mkdir()
        shutil.copy2(AUDIT, pair / "audit_claims.py")
        shutil.copy2(SCRIPTS / "check_stale.py", pair / "check_stale.py")
        rp = run(pair / "audit_claims.py", vault, "--n", "3", "--seed", "2026-Q3")
        check(rp.returncode == 0 and strata_lines(rp.stdout), f"S7: isolated pair exit {rp.returncode} / no strata\n{rp.stdout}{rp.stderr}")
        check(not (pair / "__pycache__").exists(), "S7: importing check_stale wrote __pycache__ (dont_write_bytecode regression)")
        pinned.append("S7 isolated audit_claims.py + check_stale.py pair: strata printed, no __pycache__ written")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print(f"FAIL ({len(fails)}):")
        for f in fails:
            print(f"  - {f}")
        return 1
    for p in pinned:
        print(f"  ok {p}")
    print("PASS test_audit_strata: STRATA counts pinned, queue identical to main, --record-run additive, standalone fallback")
    return 0


if __name__ == "__main__":
    sys.exit(main())
