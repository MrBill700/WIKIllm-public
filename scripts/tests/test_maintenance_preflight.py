#!/usr/bin/env python3
"""Regression test for scripts/maintenance_preflight.py -- the read-only gate
for an unattended /wiki-maintenance pass.

The gate's whole reason to exist is telling "the scanner reported zero" apart
from "the scanner did not run or did not parse". So the load-bearing case
here is: a scanner that exits 0 with garbage output must yield ready=false
and NO zero counts in the baseline. The rest cover the contract: healthy
vault passes, missing ledger blocks, non-zero exit / timeout blocks, tracked=0
is a warning not a pass, claim_audit.due comes from the claim-audit stamp
alone (another due recurring item is a warning, not a trigger -- R84),
anchor_backfill boundary decided on counts not the
rounded percentage (119/200 prints 60% but IS due; 120/200 is not), a raw
baseline that check_raw would rewrite (missing, {"files": {}}, malformed)
blocks WITHOUT check_raw running and with the file byte-identical, sync
absence and raw-pending are warnings, question-tracker discovery
(legacy: present AND named in CLAUDE.md; fallback: exactly one provable
`question_tracker: true` page, regression R189), the gate-1 edit-deny set (regression R81):
wiki/log.md by path rule, `append_only: true` by frontmatter (including a
frontmatter longer than the bounded head read), an unreadable page failing
CLOSED (denied, in `paths`, not merely warned about), an empty set that says
so in words instead of reporting a silent zero, and a warning when the vault
denies the parking destination the pass has to write; and lint.py's one
deliberate nonzero exit -- a missing or corrupt scripts/_wikilib.py
(regression R80) -- blocking with a message that names the remedy instead of a
traceback; and check_stale's B3 INVALID terminal labels and E MISSING
SCAFFOLD lines (regression R109) reaching the report as warnings -- detail lines
present verbatim, READY unchanged, the R99 ` / K UNLOGGED` header suffix
still parsing, an older check_stale without the sections parsing with no key
and no warning, and a header count the detail lines do not cover said out loud.

Builds throwaway vaults in a temp dir with the CURRENT scripts copied in,
plus a fake template dir so the sync dry run has something to compare
against. Nothing here touches a real vault.
Run: python scripts/tests/test_maintenance_preflight.py   (exit 0 = pass)
"""
import contextlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
COPIED = ["lint.py", "check_stale.py", "check_raw.py", "audit_claims.py",
          "suggest_anchors.py", "_wikilib.py", "sync_from_template.py",
          "maintenance_preflight.py"]

SOURCE_PAGE = ("---\ntitle: Manual\ndescription: A source.\n---\n"
               "# Manual\n\n## Heading A\n\nbody\n")


def claims_page(anchored: int, unanchored: int) -> str:
    """A concept page carrying source-citing claim lines; the anchor stack
    counts each as eligible and the anchored ones as covered."""
    lines = ["---", "title: Concept", "description: cites the manual.", "---",
             "# Concept", "", "## Notes", ""]
    for i in range(anchored):
        lines.append(f"- anchored claim {i} of 5 km per [[sources/manual#Heading A]]")
    for i in range(unanchored):
        lines.append(f"- bare claim {i} of 5 km per [[sources/manual]]")
    lines.append("")
    lines.append("Links: [[start-here]]")
    return "\n".join(lines) + "\n"


def build_vault(base: Path, name: str, *, ledger: bool = True, claude_md: str = "",
                anchored: int = 0, unanchored: int = 0, extra_wiki: str = "",
                init_baseline: bool = True) -> Path:
    v = base / name
    (v / "scripts").mkdir(parents=True)
    (v / "wiki" / "sources").mkdir(parents=True)
    (v / "wiki" / "concepts").mkdir()
    (v / "_meta").mkdir()
    (v / "raw").mkdir()
    for s in COPIED:
        shutil.copy2(SCRIPTS / s, v / "scripts" / s)
    (v / "wiki" / "sources" / "manual.md").write_text(SOURCE_PAGE, encoding="utf-8")
    (v / "wiki" / "start-here.md").write_text(
        "---\ntitle: Start\ndescription: entry.\n---\n# Start\n\n"
        "Entry page.\n" + extra_wiki +
        # under a see-also heading so the anchor stack does not count the
        # sources link as a claim (SKIP_HEADINGS in audit_claims)
        "\n## See also\n\n- [[concepts/concept]]\n- [[sources/manual]]\n",
        encoding="utf-8")
    (v / "wiki" / "concepts" / "concept.md").write_text(
        claims_page(anchored, unanchored), encoding="utf-8")
    if ledger:
        (v / "_meta" / "open-loops.md").write_text(
            "---\ntitle: Open Loops\ndescription: ledger.\n---\n# Open Loops\n\n"
            "Back to [[start-here]].\n", encoding="utf-8")
    if claude_md:
        (v / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    (v / "raw" / "note.txt").write_text("raw source\n", encoding="utf-8")
    if init_baseline:
        r = subprocess.run([sys.executable, str(v / "scripts" / "check_raw.py"), "--init"],
                           capture_output=True, text=True, errors="replace")
        assert r.returncode == 0, f"check_raw --init failed in fixture: {r.stdout}{r.stderr}"
    return v


def build_template(base: Path) -> Path:
    """A stand-in template so sync_from_template's dry run compares against
    something local instead of the real machine path."""
    t = base / "template"
    (t / "scripts").mkdir(parents=True)
    for s in COPIED:
        shutil.copy2(SCRIPTS / s, t / "scripts" / s)
    return t


def load_module(vault: Path):
    sys.path.insert(0, str(vault / "scripts"))
    sys.modules.pop("maintenance_preflight", None)
    try:
        return importlib.import_module("maintenance_preflight")
    finally:
        sys.path.remove(str(vault / "scripts"))
        sys.modules.pop("maintenance_preflight", None)


def main() -> int:
    fails = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        os.environ["WIKILLM_TEMPLATE"] = str(build_template(base))
        mp = load_module(build_vault(base, "loader"))

        # 1. healthy vault -> ready, no blockers, scanners ok, baseline populated
        v = build_vault(base, "healthy", anchored=3, unanchored=2)

        def file_set(root: Path) -> list:
            # __pycache__ is interpreter bytecode from lint importing the
            # anchor stack, not vault content -- ignore it.
            return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts)

        before = file_set(v)
        rep = mp.preflight(v)
        after = file_set(v)
        check(rep["ready"] is True, f"healthy: ready={rep['ready']} blockers={rep['blockers']}")
        check(rep["blockers"] == [], f"healthy: unexpected blockers {rep['blockers']}")
        check(all(s == "ok" for s in rep["scanners"].values()),
              f"healthy: scanner states {rep['scanners']}")
        check(rep["baseline"].get("all_files") == 4,
              f"healthy: all_files={rep['baseline'].get('all_files')} expected 4")
        check(rep["baseline"].get("raw_files") == 1 and rep["baseline"].get("raw_added") == 0,
              f"healthy: raw counts {rep['baseline']}")
        check(rep["park_to"] == str(v / "_meta" / "open-loops.md"),
              f"healthy: park_to={rep['park_to']}")
        check(rep["template_sync"]["status"] in ("in_sync", "drifted")
              and isinstance(rep["template_sync"]["in_sync"], int),
              f"healthy: template_sync={rep['template_sync']}")
        check(before == after, "healthy: preflight modified the vault file set")
        # anchor boundary: 3/5 = 60% == target 60% -> NOT due
        ab = rep["triggers"]["anchor_backfill"]
        check(ab["eligible"] == 5 and ab["coverage_pct"] == 60 and ab["target_pct"] == 60,
              f"boundary: anchor numbers {ab}")
        check(ab["due"] is False, f"boundary: coverage == target must not be due: {ab}")
        # tracked == 0 -> due false AND a warning (never a silent pass)
        ca = rep["triggers"]["claim_audit"]
        check(ca["tracked"] == 0 and ca["due"] is False and ca["note"],
              f"tracked=0: claim_audit={ca}")
        check(any("cadence" in w for w in rep["warnings"]),
              f"tracked=0: no cadence warning in {rep['warnings']}")
        # no CLAUDE.md at all -> question_tracker null + warning
        check(rep["question_tracker"] is None, f"healthy: question_tracker={rep['question_tracker']}")
        check(any("question tracker" in w for w in rep["warnings"]),
              f"healthy: no question-tracker warning in {rep['warnings']}")

        # 2. anchor_backfill due when eligible > 0 and coverage < target
        v = build_vault(base, "lowcov", anchored=2, unanchored=3)
        ab = mp.preflight(v)["triggers"]["anchor_backfill"]
        check(ab["due"] is True and ab["coverage_pct"] == 40, f"lowcov: {ab}")

        # 3. anchor_backfill NOT due when eligible == 0 (lint's 'no source-citing claims' line)
        v = build_vault(base, "noclaims")
        rep = mp.preflight(v)
        ab = rep["triggers"]["anchor_backfill"]
        check(rep["scanners"]["lint.py"] == "ok", f"noclaims: lint={rep['scanners']['lint.py']}")
        check(ab["eligible"] == 0 and ab["due"] is False, f"noclaims: {ab}")

        # 4. claim-audit stamp installed and due -> due true, no cadence warning
        v = build_vault(base, "stamped",
                        extra_wiki="\nClaim audit -- Last run: 2026-01-05 / Next due: ~2026-04\n")
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["tracked"] == 1 and ca["due_count"] == 1 and ca["due"] is True,
              f"stamped: {ca}")
        check(ca["recurring_due_other"] == 0 and ca["stamp"] and "start-here.md" in ca["stamp"],
              f"stamped: other/stamp {ca}")
        check(not any("cadence" in w for w in rep["warnings"]),
              f"stamped: spurious cadence warning {rep['warnings']}")

        # 4b. R84: ANOTHER recurring item is due, the claim audit is not ->
        # due false, other=1 + a warning (sample-vault-c's "deeper claim audit"
        # wording; the template CLAUDE.md wording is case 4f).
        v = build_vault(base, "otherdue", extra_wiki=(
            "\n- **Every 8-12 weeks: drift-test retest** -- **Last run: 2026-07-20. "
            "Next due: ~2026-09.**\n"
            "- **Quarterly: deeper claim audit** -- run `python scripts/audit_claims.py`. "
            "**Last run: 2026-07-29. Next due: ~2999-10.**\n"))
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["tracked"] == 2 and ca["due_count"] == 1, f"otherdue: section D counts {ca}")
        check(ca["due"] is False, f"otherdue: fired on a non-claim-audit stamp: {ca}")
        check(ca["recurring_due_other"] == 1 and "2999-10" in (ca["stamp"] or ""),
              f"otherdue: other/stamp {ca}")
        check(any("other recurring item" in w for w in rep["warnings"]),
              f"otherdue: no other-due warning in {rep['warnings']}")
        check(not any("cadence" in w for w in rep["warnings"]),
              f"otherdue: spurious cadence warning {rep['warnings']}")

        # 4c. both due -> due true, other=1
        v = build_vault(base, "bothdue", extra_wiki=(
            "\n- retest -- Last run: 2026-07-20. Next due: ~2026-09.\n"
            "- Quarterly: claim audit -- Last run: never. Next due: ~2026-01.\n"))
        ca = mp.preflight(v)["triggers"]["claim_audit"]
        check(ca["due"] is True and ca["due_count"] == 2 and ca["recurring_due_other"] == 1,
              f"bothdue: {ca}")

        # 4d. stamps tracked but NONE names the claim audit -> due false,
        # cadence warning (the R79 state: '1 tracked' was read as installed)
        v = build_vault(base, "wrongstamp",
                        extra_wiki="\nCuration pass -- Last run: 2026-01-05 / Next due: ~2026-04\n")
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["tracked"] == 1 and ca["due"] is False and ca["stamp"] is None,
              f"wrongstamp: {ca}")
        check(ca["recurring_due_other"] == 1, f"wrongstamp: other {ca}")
        check(any("cadence not installed" in w for w in rep["warnings"]),
              f"wrongstamp: no cadence warning in {rep['warnings']}")

        # 4h. R79: a recorded exemption in a vault with NO source-citing
        # claims -> due false, exempt set (locator suffix stripped), NO cadence
        # warning; other due items still surface
        v = build_vault(base, "exempt", extra_wiki=(
            "\nclaim audit: exempt -- this vault cites sources inline, no source-citing claims\n"
            "\nCuration pass -- Last run: never. Next due: ~2026-01\n"))
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["due"] is False and ca["exempt"] and "inline" in ca["exempt"]
              and "[" not in ca["exempt"] and ca["recurring_due_other"] == 1, f"exempt: {ca}")
        check(not any("cadence" in w or "EXEMPT but" in w for w in rep["warnings"]),
              f"exempt: spurious warning {rep['warnings']}")
        check(any("recurring item" in w for w in rep["warnings"]),
              f"exempt: other-due not surfaced {rep['warnings']}")
        # exemption in a vault that HAS source-citing claims -> the criterion
        # does not hold: exempt still recorded, but a warning names the count
        v = build_vault(base, "exempt_wrong", anchored=3, unanchored=2,
                        extra_wiki="\n- **Claim audit: exempt** (no claims here, honest)\n")
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["exempt"] and ca["due"] is False, f"exempt_wrong: {ca}")
        check(any("EXEMPT but lint counts 5" in w for w in rep["warnings"]),
              f"exempt_wrong: criterion warning missing {rep['warnings']}")
        # the template placeholder must NOT exempt: cadence warning stays
        v = build_vault(base, "placeholder", extra_wiki="\nclaim audit: exempt -- <reason>\n")
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["exempt"] is None and any("cadence" in w for w in rep["warnings"]),
              f"placeholder must not exempt: {ca} {rep['warnings']}")
        # exemption + installed stamp -> conflict warning, due still honest
        v = build_vault(base, "conflict", extra_wiki=(
            "\nclaim audit: exempt -- reason\n"
            "\n- Quarterly: claim audit -- Last run: never. Next due: ~2026-01.\n"))
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["due"] is True and ca["exempt"] is None, f"conflict: {ca}")
        check(any("coexist" in w for w in rep["warnings"]), f"conflict: {rep['warnings']}")

        # 4e. log.md is excluded (an append-only log mentions past stamps):
        # a non-log stamp keeps tracked >= 1 so claim_audit_stamps actually
        # runs; the only claim-audit stamp lives in log.md -> not installed.
        v = build_vault(base, "logonly",
                        extra_wiki="\nCuration pass -- Last run: never. Next due: ~2999-01\n")
        (v / "wiki" / "log.md").write_text(
            "# Log\n\nclaim audit -- Last run: never. Next due: ~2026-01.\n", encoding="utf-8")
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["tracked"] == 1 and ca["due"] is False and ca["stamp"] is None,
              f"logonly: {ca}")
        check(any("cadence not installed" in w for w in rep["warnings"]),
              f"logonly: no cadence warning in {rep['warnings']}")

        # 4f. the template's own install block (_meta/open-loops.md): the
        # item name is three lines above the stamp. Must count as installed
        # and due; other=0. The heading line above must not leak in.
        v = build_vault(base, "multiline", extra_wiki=(
            "\n## Recurring\n\n"
            "- **Quarterly: claim audit** -- run `python scripts/audit_claims.py`, verify each\n"
            "  sampled claim against its `sources/` page, judge FAITHFUL / DRIFTED,\n"
            "  fix drift immediately, log it as the script's NEXT STEPS block prints.\n"
            "  **Last run: never. Next due: ~2026-01.**\n"))
        rep = mp.preflight(v)
        ca = rep["triggers"]["claim_audit"]
        check(ca["tracked"] == 1 and ca["due"] is True and ca["recurring_due_other"] == 0
              and ca["stamp"] and "2026-01" in ca["stamp"], f"multiline: {ca}")
        check(not any("cadence" in w or "other recurring" in w for w in rep["warnings"]),
              f"multiline: spurious warning {rep['warnings']}")
        # the block stops at a list-item boundary: a claim-audit item directly
        # ABOVE a retest item must not lend it the token
        v = build_vault(base, "neighbour", extra_wiki=(
            "\n- **Quarterly: claim audit** -- Last run: 2026-01-01. Next due: ~2999-01.\n"
            "- **Retest** -- treadmill.\n"
            "  **Last run: never. Next due: ~2026-01.**\n"))
        ca = mp.preflight(v)["triggers"]["claim_audit"]
        check(ca["tracked"] == 2 and ca["due"] is False and ca["recurring_due_other"] == 1,
              f"neighbour: {ca}")

        # callout-wrapped install block (> - item / >   stamp) with prose above:
        # the list marker inside the quote is still the block start
        v = build_vault(base, "callout", extra_wiki=(
            "\nSome prose about audits that must not be swept in.\n"
            "> [!todo]\n"
            "> - **Quarterly: claim audit** -- run the script.\n"
            ">   **Last run: never. Next due: ~2026-01.**\n"))
        ca = mp.preflight(v)["triggers"]["claim_audit"]
        check(ca["due"] is True and ca["recurring_due_other"] == 0, f"callout: {ca}")
        # a table row / prose stamp is judged on its own line: the claim-audit
        # row above must not lend the curation row its token
        v = build_vault(base, "tablerow", extra_wiki=(
            "\n| Item | Stamp |\n|---|---|\n"
            "| Claim audit | Last run: never. Next due: ~2999-01 |\n"
            "| Curation pass | Last run: never. Next due: ~2026-01 |\n"))
        ca = mp.preflight(v)["triggers"]["claim_audit"]
        check(ca["tracked"] == 2 and ca["due"] is False and ca["recurring_due_other"] == 1,
              f"tablerow: {ca}")

        # 4g. piggyback line: a non-audit cadence that MENTIONS the audit on
        # the same item fires due=true -- documented, not fixed (the launcher
        # re-reads `stamp` per SKILL.md section 6). This pins the behaviour so
        # a change to it is deliberate.
        v = build_vault(base, "piggyback", extra_wiki=(
            "\n- Quarterly: curation pass, piggyback the claim audit -- "
            "Last run: never. Next due: ~2026-01.\n"))
        ca = mp.preflight(v)["triggers"]["claim_audit"]
        check(ca["due"] is True and ca["stamp"], f"piggyback: {ca}")

        # 5. missing _meta/open-loops.md -> blocked, blocker names it
        v = build_vault(base, "noledger", ledger=False)
        rep = mp.preflight(v)
        check(rep["ready"] is False, "noledger: ready should be False")
        check(rep["park_to"] is None, f"noledger: park_to={rep['park_to']}")
        check(any("open-loops.md" in b for b in rep["blockers"]),
              f"noledger: blockers {rep['blockers']}")
        check(not (v / "_meta" / "open-loops.md").exists(), "noledger: gate CREATED the ledger")

        # 6. scanner exits non-zero -> blocked
        v = build_vault(base, "crash")
        (v / "scripts" / "check_stale.py").write_text(
            "import sys\nprint('=== A. OPEN-LOOPS LEDGER === 0 open / 0 overdue')\nsys.exit(3)\n",
            encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["ready"] is False, "crash: ready should be False")
        check(rep["scanners"]["check_stale.py"].startswith("exit 3"),
              f"crash: scanner state {rep['scanners']['check_stale.py']}")
        check("open_loops_open" not in rep["baseline"],
              "crash: partial output from a failed scanner leaked into baseline")

        # 7. THE test: scanner exits 0 with unparseable output -> blocked, NOT zeros
        v = build_vault(base, "garbage")
        (v / "scripts" / "lint.py").write_text(
            "print('=== ALL FILES === 4')\nprint('=== BROKEN LINKS === lots')\n",
            encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["ready"] is False, "garbage: ready should be False")
        check(rep["scanners"]["lint.py"].startswith("unparseable"),
              f"garbage: scanner state {rep['scanners']['lint.py']}")
        check(any("lint.py" in b and "not zero" in b for b in rep["blockers"]),
              f"garbage: blocker wording {rep['blockers']}")
        for k in ("broken_links", "orphans", "ghost_links", "anchor_eligible", "all_files"):
            check(k not in rep["baseline"], f"garbage: baseline has {k}={rep['baseline'].get(k)}")
        check(rep["triggers"]["anchor_backfill"]["due"] is False
              and rep["triggers"]["anchor_backfill"]["eligible"] is None,
              f"garbage: anchor trigger fabricated {rep['triggers']['anchor_backfill']}")
        # variant: empty output, exit 0
        (v / "scripts" / "lint.py").write_text("pass\n", encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["ready"] is False and "all_files" not in rep["baseline"],
              f"empty-output: ready={rep['ready']} baseline={rep['baseline']}")

        # 7b. lint.py's ONE nonzero exit (regression R80): _wikilib.py missing or
        # corrupt. lint parses links through the shared library with no
        # fallback parser, so this must surface as a blocker whose text names
        # the remedy -- preflight reports _first_line(stderr), so an uncaught
        # SyntaxError would make the operator-facing blocker read literally
        # "Traceback (most recent call last):".
        for label, mutate in (
                ("nolib", lambda p: p.unlink()),
                ("corruptlib", lambda p: p.write_text('"""truncated\n',
                                                      encoding="utf-8")),
        ):
            v = build_vault(base, label)
            mutate(v / "scripts" / "_wikilib.py")
            rep = mp.preflight(v)
            check(rep["ready"] is False, f"{label}: ready should be False")
            check(rep["scanners"]["lint.py"].startswith("exit 1"),
                  f"{label}: scanner state {rep['scanners']['lint.py']}")
            blocker = next((b for b in rep["blockers"] if "lint.py" in b), "")
            check("_wikilib.py" in blocker and "sync_from_template.py" in blocker,
                  f"{label}: blocker does not name the remedy: {rep['blockers']}")
            check("Traceback" not in blocker,
                  f"{label}: blocker is a traceback, not a message: {blocker}")
            check("all_files" not in rep["baseline"],
                  f"{label}: baseline fabricated from a failed lint {rep['baseline']}")

        # 8. missing raw baseline -> check_raw NOT invoked (no write), blocked
        v = build_vault(base, "nobaseline", init_baseline=False)
        rep = mp.preflight(v)
        check(rep["ready"] is False, "nobaseline: ready should be False")
        check(rep["scanners"]["check_raw.py"].startswith("not run"),
              f"nobaseline: {rep['scanners']['check_raw.py']}")
        check(not (v / "_meta" / "raw-hashes.json").exists(),
              "nobaseline: gate let check_raw WRITE a baseline")

        # 9. missing scanner file -> blocked
        v = build_vault(base, "noscanner")
        (v / "scripts" / "check_raw.py").unlink()
        rep = mp.preflight(v)
        check(rep["ready"] is False and rep["scanners"]["check_raw.py"] == "missing",
              f"noscanner: {rep['scanners']}")

        # 10. not a vault -> blocked
        rep = mp.preflight(base / "nowhere")
        check(rep["ready"] is False and any("not a vault" in b for b in rep["blockers"]),
              f"notvault: {rep['blockers']}")

        # 11. question_tracker discovery
        # present + named in CLAUDE.md -> found
        v = build_vault(base, "qt_found", claude_md="Questions go in `_meta/open-questions.md`.\n")
        (v / "_meta" / "open-questions.md").write_text("# Q\n", encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["question_tracker"] == str(v / "_meta" / "open-questions.md"),
              f"qt_found: {rep['question_tracker']}")
        check(not any("question tracker" in w for w in rep["warnings"]),
              f"qt_found: spurious warning {rep['warnings']}")
        # present but NOT named -> null
        v = build_vault(base, "qt_unnamed", claude_md="Nothing about trackers here.\n")
        (v / "_meta" / "for-reviewer.md").write_text("# Q\n", encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["question_tracker"] is None, f"qt_unnamed: {rep['question_tracker']}")
        # named but NOT present -> null, and never created
        v = build_vault(base, "qt_absent", claude_md="See `_meta/for-reviewer.md` for questions.\n")
        rep = mp.preflight(v)
        check(rep["question_tracker"] is None, f"qt_absent: {rep['question_tracker']}")
        check(not (v / "_meta" / "for-reviewer.md").exists(), "qt_absent: gate CREATED the tracker")

        # 11b. `question_tracker: true` fallback (regression R189). resolve_question_tracker
        # is exercised directly -- it reads only CLAUDE.md, wiki/ and _meta/ -- plus
        # one end-to-end run for the report keys.
        def qt_vault(name: str, claude_md: str = "", pages: dict | None = None) -> Path:
            q = base / name
            (q / "wiki" / "network").mkdir(parents=True)
            (q / "_meta").mkdir()
            if claude_md:
                (q / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
            for rel, text in (pages or {}).items():
                (q / rel).parent.mkdir(parents=True, exist_ok=True)
                (q / rel).write_text(text, encoding="utf-8")
            return q

        FLAGGED = "---\ntitle: Queue\nquestion_tracker: true\n---\n# Queue\n"
        PLAIN = "---\ntitle: Plain\n---\n# Plain\n"
        # one flagged page, NOT named in CLAUDE.md -> chosen; the flag is self-authenticating
        q = qt_vault("qt_flag", claude_md="Nothing named.\n",
                     pages={"wiki/network/queue.md": FLAGGED, "wiki/network/other.md": PLAIN})
        path, src, w = mp.resolve_question_tracker(q)
        check(path == str(q / "wiki/network/queue.md") and src == "flag" and w == [],
              f"qt_flag: {path} {src} {w}")
        # tracker_page: true is NOT a question tracker; nor is the flag in the body or false
        q = qt_vault("qt_notflag", pages={
            "wiki/network/proj.md": "---\ntitle: P\ntracker_page: true\n---\n# P\n",
            "wiki/network/body.md": "---\ntitle: B\n---\nquestion_tracker: true\n",
            "wiki/network/off.md": "---\ntitle: O\nquestion_tracker: false\n---\n"})
        path, src, w = mp.resolve_question_tracker(q)
        check(path is None and src is None, f"qt_notflag: chose {path} ({src})")
        check(len(w) == 1 and "question_tracker: true" in w[0] and "open-loops" in w[0],
              f"qt_notflag: warning should name the flag and the fallback: {w}")
        # two flagged -> unresolved, never a guess
        q = qt_vault("qt_two", pages={"wiki/network/a.md": FLAGGED, "_meta/b.md": FLAGGED})
        path, src, w = mp.resolve_question_tracker(q)
        check(path is None and src is None, f"qt_two: chose {path} ({src})")
        check(len(w) == 1 and "2 pages" in w[0] and "exactly one" in w[0], f"qt_two: {w}")
        # one flagged + one unreadable -> uniqueness unknown -> unresolved (fail closed)
        q = qt_vault("qt_unread", pages={"wiki/network/queue.md": FLAGGED})
        (q / "wiki" / "network" / "locked.md").mkdir()
        path, src, w = mp.resolve_question_tracker(q)
        check(path is None and src is None,
              f"qt_unread: chose {path} ({src}) with an unreadable page -- fails OPEN")
        check(len(w) == 1 and "1 page(s) unreadable" in w[0] and "cannot be proven" in w[0]
              and "wiki/network/locked.md" in w[0], f"qt_unread: {w}")
        # unreadable page + 2 flagged: the warning names both causes
        q = qt_vault("qt_unread_two", pages={"wiki/network/a.md": FLAGGED, "_meta/b.md": FLAGGED})
        (q / "wiki" / "network" / "locked.md").mkdir()
        path, src, w = mp.resolve_question_tracker(q)
        check(path is None and len(w) == 1 and "2 readable page(s) are flagged" in w[0],
              f"qt_unread_two: {path} {w}")
        # a DIRECTORY that cannot be listed hides its pages; rglob skips it
        # silently, so this is the fail-OPEN case -- it must count as unreadable.
        # Real ACL (read-data deny only, never a full F deny under %TEMP%),
        # undone in finally and the undo verified loudly. Windows only.
        if os.name == "nt" and shutil.which("icacls"):
            q = qt_vault("qt_acl", pages={"wiki/network/vis.md": FLAGGED,
                                          "wiki/priv/hidden.md": FLAGGED})
            priv = q / "wiki" / "priv"
            user = os.environ.get("USERNAME", "")
            deny = subprocess.run(["icacls", str(priv), "/deny", f"{user}:(OI)(CI)(RD)"],
                                  capture_output=True, text=True)
            try:
                listable = True
                try:
                    os.listdir(priv)
                except PermissionError:
                    listable = False
                if deny.returncode == 0 and not listable:
                    path, src, w = mp.resolve_question_tracker(q)
                    check(path is None and src is None,
                          f"qt_acl: chose {path} ({src}) past an unlistable dir -- fails OPEN")
                    check(len(w) == 1 and "wiki/priv/" in w[0] and "cannot be proven" in w[0],
                          f"qt_acl: {w}")
                else:
                    # icacls exists, so a deny that did not take is a setup
                    # failure, never a silent skip under a PASS line
                    check(False, f"qt_acl: could not make {priv} unlistable "
                                 f"(deny rc={deny.returncode}, listable={listable})")
            finally:
                subprocess.run(["icacls", str(priv), "/remove:d", user], capture_output=True)
                try:
                    os.listdir(priv)
                except OSError as e:
                    check(False, f"qt_acl: ACL undo FAILED, {priv} still unlistable: {e}")
        # a junction is not walked (it can alias the vault into itself); what it
        # hides is unknown -> unreadable -> unresolved. mklink /J needs no privilege.
        if os.name == "nt":
            q = qt_vault("qt_junction", pages={"wiki/network/vis.md": FLAGGED})
            (base / "qt_junction_target").mkdir()
            (base / "qt_junction_target" / "x.md").write_text(FLAGGED, encoding="utf-8")
            mk = subprocess.run(["cmd", "/c", "mklink", "/J", str(q / "wiki" / "jn"),
                                 str(base / "qt_junction_target")], capture_output=True, text=True)
            check(mk.returncode == 0, f"qt_junction: mklink failed {mk.stdout} {mk.stderr}")
            path, src, w = mp.resolve_question_tracker(q)
            check(path is None and len(w) == 1 and "wiki/jn/" in w[0]
                  and "x.md" not in w[0], f"qt_junction: {path} {w}")
            os.rmdir(q / "wiki" / "jn")  # removes the junction only, never the target
        # strict frontmatter parsing (external review): only a TOP-LEVEL
        # key with an exact boolean true selects; indented text (a block-scalar
        # description, a nested mapping) is not the key; any other value of a
        # top-level key, or the key twice, is AMBIGUOUS and fails closed; a tab
        # separator is valid YAML and must count, so duplicates are not missed.
        for name, pages, want in [
            ("qt_p_block", {"wiki/a.md": "---\ntitle: A\ndescription: |\n  question_tracker: true\n---\n"}, None),
            ("qt_p_nested", {"wiki/a.md": "---\nmeta:\n  question_tracker: true\n---\n"}, None),
            ("qt_p_tabdup", {"wiki/a.md": FLAGGED, "wiki/b.md": "---\nquestion_tracker:\ttrue\n---\n"}, None),
            ("qt_p_trueish", {"wiki/a.md": "---\nquestion_tracker: truely\n---\n"}, None),
            ("qt_p_quoted", {"wiki/a.md": "---\nquestion_tracker: \"true\"\n---\n"}, None),
            ("qt_p_twice", {"wiki/a.md": "---\nquestion_tracker: true\nquestion_tracker: false\n---\n"}, None),
            ("qt_p_True", {"wiki/a.md": "---\nquestion_tracker: True\n---\n"}, "wiki/a.md"),
            ("qt_p_comment", {"wiki/a.md": "---\nquestion_tracker: true  # home\n---\n"}, "wiki/a.md"),
            ("qt_p_spaces", {"wiki/a.md": "---\nquestion_tracker :\ttrue\n---\n"}, "wiki/a.md"),
            ("qt_p_crlf", {"wiki/a.md": "---\r\nquestion_tracker: true\r\n---\r\n"}, "wiki/a.md"),
            ("qt_p_false", {"wiki/a.md": "---\nquestion_tracker: false\n---\n"}, None),
            # prose QUOTING the flag is not a key line: it neither poisons nor
            # duplicates a real flag (the owner's ruling 2026-10-03, restoring the
            # grill's expectation) -- block-scalar, quoted and plain values
            ("qt_p_desc_plus_real", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\ndescription: |\n  question_tracker: true\n---\n"}, "wiki/a.md"),
            ("qt_p_quoted_desc_plus_real", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\ndescription: \"Add question_tracker: true when this becomes the tracker.\"\n---\n"},
             "wiki/a.md"),
            ("qt_p_nested_plus_real", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\nmeta:\n  question_tracker: true\n---\n"}, "wiki/a.md"),
            # ...but a case-variant TOP-LEVEL key is a key line, non-canonical
            ("qt_p_case_key", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\nQuestion_Tracker: true\n---\n"}, None),
            ("qt_p_continued", {"wiki/a.md": "---\nquestion_tracker: true\n  story\n---\n"}, None),
            ("qt_p_nospace", {"wiki/a.md": "---\nquestion_tracker:true\n---\n"}, None),
            ("qt_p_nextline", {"wiki/a.md": FLAGGED, "wiki/b.md": "---\nquestion_tracker:\n  true\n---\n"}, None),
            ("qt_p_comment_nextline", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\nquestion_tracker: # chosen\n  true\n---\n"}, None),
            ("qt_p_quoted_key", {"wiki/a.md": FLAGGED, "wiki/b.md": "---\n\"question_tracker\": true\n---\n"}, None),
            ("qt_p_four_dash", {"wiki/a.md": "----\nquestion_tracker: true\n----\n# body\n"}, None),
            ("qt_p_dashkey", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\n---label: x\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_bom_dup", {"wiki/a.md": FLAGGED, "wiki/b.md": "\ufeff" + FLAGGED}, None),
            ("qt_p_unclosed", {"wiki/a.md": FLAGGED, "wiki/b.md": "---\nquestion_tracker: true\n"}, None),
            ("qt_p_body_mention", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\ntitle: B\n---\nquestion_tracker: true is how you flag it\n"}, "wiki/a.md"),
            # block-shape proof (Codex r3): flow/JSON frontmatter is ambiguous
            # even with no literal mention of the key
            ("qt_p_flow_nest", {"wiki/a.md": "---\n{\nmeta: {\nquestion_tracker: true\n}\n}\n---\n"}, None),
            ("qt_p_flow_value", {"wiki/a.md": "---\nmeta: {\nquestion_tracker: true\n}\n---\n"}, None),
            ("qt_p_json_escape", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\n{\"question_\\u0074racker\": true}\n---\n"}, None),
            ("qt_p_open_list", {"wiki/a.md": FLAGGED, "wiki/b.md": "---\ntags: [a,\n  b]\n---\n"}, None),
            # a commented-out close bracket does not close the list
            # (single page: the canonical line is the ONLY flag, so only the
            # nesting proof stands between it and selection)
            ("qt_p_comment_bracket", {"wiki/b.md":
                "---\ntags: [a, # ]\nquestion_tracker: true\n]\n---\n"}, None),
            ("qt_p_open_quote", {"wiki/b.md":
                "---\ntitle: \"multi\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_open_squote", {"wiki/b.md":
                "---\ntitle: 'it''s\nquestion_tracker: true\n---\n"}, None),
            # Codex r4: anchors/tags open an unproven value; an indented root hides keys
            ("qt_p_anchor", {"wiki/b.md":
                "---\ntitle: &a \"hello\nquestion_tracker: true\nend: world\"\n---\n"}, None),
            ("qt_p_tag", {"wiki/b.md":
                "---\ntitle: !!str \"hello\nquestion_tracker: true\nend: world\"\n---\n"}, None),
            ("qt_p_indented_root", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\n  \"question_\\u0074racker\": true\n---\n"}, None),
            # complex-but-closed shapes on an UNFLAGGED page do not poison
            # duplicate detection; the flag sits on a simple page
            ("qt_p_block_header_ok", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\ndescription: >-\n  folded text\nnotes: |2  # c\n   kept\n---\n"},
             "wiki/a.md"),
            # Codex r5: a quote/flow opened on an INDENTED line
            ("qt_p_indented_quote", {"wiki/a.md":
                "---\ntitle:\n  \"hello\nquestion_tracker: true\nend: world\"\n---\n"}, None),
            ("qt_p_indented_item_flow", {"wiki/a.md":
                "---\nlist:\n  - [a,\nquestion_tracker: true\nb: ]\n---\n"}, None),
            # Codex r6: explicit mapping indicators; whitelist closes the class
            ("qt_p_explicit_map", {"wiki/a.md":
                "---\nmeta:\n  ? title\n  : \"hello\nquestion_tracker: true\nend: world\"\n---\n"}, None),
            ("qt_p_top_explicit", {"wiki/a.md":
                "---\ntitle: ? x\nquestion_tracker: true\n---\n"}, None),
            # Codex r7: node properties INSIDE a flow collection
            ("qt_p_flow_anchor", {"wiki/a.md":
                "---\ntitle: [&a \"hello ]\nquestion_tracker: true\nend: world\"]\n---\n"}, None),
            ("qt_p_flow_tag", {"wiki/a.md":
                "---\ntitle: [!!str \"hello ]\nquestion_tracker: true\nend: world\"]\n---\n"}, None),
            ("qt_p_flow_map_anchor", {"wiki/a.md":
                "---\ntitle: {k: &a \"hello }\nquestion_tracker: true\nend: world\"}\n---\n"}, None),
            ("qt_p_flow_plain_ok", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\ntags: [net, \"a, b\", {k: v}, 'it''s']  # ok\n---\n"},
             "wiki/a.md"),
            # non-ASCII starts are plain (real sample-vault-f pages: source_quality: <check-mark emoji> ...)
            ("qt_p_emoji_ok", {"wiki/a.md":
                "---\nsource_quality: \u2705 Primary\nnote: \u26a0\ufe0f x\nquestion_tracker: true\n---\n"},
             "wiki/a.md"),
            # block-scalar text may look like anything; plain nested values are fine
            ("qt_p_block_text_ok", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\nnotes: |\n  * bullet \"unclosed\n  [also open\naliases:\n  - Bob's [x]\n"
                "  - key: 'ok'\n---\n"}, "wiki/a.md"),
            # simple-grammar rule (advisor, after Codex r7): the FLAGGED page
            # itself must be simple -- any quote / block scalar / nested
            # mapping / anchor on it is ambiguous, closing the r1-r7 class
            ("qt_p_g_quoted_desc", {"wiki/a.md":
                "---\ndescription: \"x\"\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_g_block", {"wiki/a.md":
                "---\nnotes: |\n  text\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_g_nested", {"wiki/a.md":
                "---\nmeta:\n  k: v\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_g_flow_anchor", {"wiki/a.md":
                "---\nt: [&a x]\nquestion_tracker: true\n---\n"}, None),
            # mid-value indicators are literal in a plain scalar (sample-vault-a
            # pending-decisions description carries `[!todo]` and "Bob's")
            ("qt_p_g_midvalue_ok", {"wiki/a.md":
                "---\ndescription: index of open items (where [!todo] lives) -- Bob's & co *\n"
                "question_tracker: true\n---\n"}, "wiki/a.md"),
            ("qt_p_g_flow_bracket", {"wiki/a.md":
                "---\nt: [a, b] c]\nquestion_tracker: true\n---\n"}, None),
            # Codex r8: separators splitlines() honours but YAML does not
            ("qt_p_u2028", {"wiki/a.md": "---\ntitle: A\n# example\u2028question_tracker: true\n---\n"}, None),
            ("qt_p_u2029", {"wiki/a.md": "---\ntitle: A\n# example\u2029question_tracker: true\n---\n"}, None),
            ("qt_p_nel", {"wiki/a.md": "---\ntitle: A\n# example\x85question_tracker: true\n---\n"}, None),
            # Codex r9 + the owner's lexical ruling: YAML-invalid characters, checked
            # BEFORE strip() -- one case per forbidden class
            ("qt_p_vt", {"wiki/a.md": "---\n\x0b\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_ff", {"wiki/a.md": "---\ntitle: A\x0c\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_c0_in_comment", {"wiki/a.md": "---\n# note\x01\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_nul", {"wiki/a.md": "---\ntitle: A\x00\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_del", {"wiki/a.md": "---\ntitle: A\x7f\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_c1", {"wiki/a.md": "---\ntitle: A\x9b\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_fffe", {"wiki/a.md": "---\ntitle: A\ufffe\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_ffff", {"wiki/a.md": "---\ntitle: A\uffff\nquestion_tracker: true\n---\n"}, None),
            # Codex r10: a forbidden char on a FENCE line must not be trimmed away
            ("qt_p_close_fence_vt", {"wiki/a.md":
                "---\nquestion_tracker: true\n---\x0b\nquestion_tracker: false\n---\n"}, None),
            ("qt_p_close_fence_vt_eof", {"wiki/a.md": "---\nquestion_tracker: true\n---\x0b\n"}, None),
            ("qt_p_open_fence_vt", {"wiki/a.md": "---\x0b\nquestion_tracker: true\n---\n"}, None),
            ("qt_p_open_fence_ff", {"wiki/a.md": "---\x0c\nquestion_tracker: true\n---\n"}, None),
            # ASCII space/tab after a fence is still a fence
            ("qt_p_fence_ascii_ws_ok", {"wiki/a.md": "--- \t\nquestion_tracker: true\n---\t \n"}, "wiki/a.md"),
            # ...whereas a lone CR IS a YAML 1.2 line break: the flag after it is
            # a real top-level line, so selecting it is correct
            ("qt_p_lone_cr", {"wiki/a.md": "---\ntitle: A\n# example\rquestion_tracker: true\n---\n"},
             "wiki/a.md"),
            ("qt_p_g_real_shape_ok", {"wiki/a.md":
                "---\ntitle: Verification queue (unconfirmed facts)\n"
                "description: Tracker page -- facts to confirm; open items expected.\n"
                "tags: [network, tracker]\nupdated: 2026-09-03\ntracker_page: true\n"
                "aliases:\n  - Queue\nquestion_tracker: true  # regression R189\n---\n"}, "wiki/a.md"),
            # a `#` inside a quoted list item is text, not a comment (real
            # page shape: aliases: ["Registration of #STRKR ..."])
            ("qt_p_quoted_hash", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\naliases: [\"Registration of #STRKR Barley (2015)\"]\n"
                "title: 'it''s [not] open'\n---\n"}, "wiki/a.md"),
            # an apostrophe INSIDE a plain flow item is text (real vault
            # pages: aliases: [..., Reviewer's archive])
            ("qt_p_apostrophe", {"wiki/a.md": FLAGGED, "wiki/b.md":
                "---\naliases: [Example Project, Reviewer's archive]\n---\n"}, "wiki/a.md"),
            # ...but a quote at a node start still opens a quoted scalar
            ("qt_p_node_quote", {"wiki/b.md":
                "---\naliases: [a, 'open\nquestion_tracker: true\n---\n"}, None),
            # controls: ordinary block frontmatter beside the flag still resolves
            ("qt_p_block_ok", {"wiki/a.md": "---\ntitle: A\ndate created: 2026-10-03\n"
                "tags: [net, tracker]  # closed\naliases:\n  - Q\n# note\n\n"
                "question_tracker: true\n---\n", "wiki/b.md": "---\nsources: [\"[[x\\|y]]\"]\n---\n"},
             "wiki/a.md"),
        ]:
            q = qt_vault(name)
            for rel, text in pages.items():
                (q / rel).parent.mkdir(parents=True, exist_ok=True)
                (q / rel).write_bytes(text.encode("utf-8"))
            path, src, w = mp.resolve_question_tracker(q)
            got = Path(path).relative_to(q).as_posix() if path else None
            check(got == want, f"{name}: got {got} want {want} warnings={w}")
        # a surrogate cannot be written as UTF-8 text; its real-file forms are
        # an encoded surrogate (ED A0 80) or any invalid byte -- not YAML, and a
        # replace-decoded read would hide them -> ambiguous
        for name, raw in [("qt_p_surrogate_bytes", b"---\ntitle: A\xed\xa0\x80\nquestion_tracker: true\n---\n"),
                          ("qt_p_bad_utf8", b"---\ntitle: A\xff\nquestion_tracker: true\n---\n")]:
            q = qt_vault(name)
            (q / "wiki" / "a.md").write_bytes(raw)
            path, src, w = mp.resolve_question_tracker(q)
            check(path is None and src is None and len(w) == 1 and "wiki/a.md" in w[0],
                  f"{name}: {path} {src} {w}")
        # an ambiguous value names itself in the warning
        q = qt_vault("qt_p_ambig_warn", pages={"wiki/network/a.md": "---\nquestion_tracker: yes\n---\n"})
        path, src, w = mp.resolve_question_tracker(q)
        check(path is None and len(w) == 1 and "ambiguous" in w[0] and "wiki/network/a.md" in w[0],
              f"qt_p_ambig_warn: {w}")
        # ambiguity never touches a legacy winner: selection, source, no warning
        q = qt_vault("qt_legacy_ambig", claude_md="Questions: `_meta/open-questions.md`.\n",
                     pages={"_meta/open-questions.md": "# Q\n",
                            "wiki/network/x.md": "---\ntitle: &a \"x\nquestion_tracker: yes\n---\n"})
        path, src, w = mp.resolve_question_tracker(q)
        check(path == str(q / "_meta/open-questions.md") and src == "legacy" and w == [],
              f"qt_legacy_ambig: {path} {src} {w}")
        # ...and an unproven "flag" (inside an indented quote, Codex r5; behind
        # a flow anchor, r7) earns no false redundancy warning beside the winner
        for name, text in [
            ("qt_legacy_ambig2", "---\ntitle:\n  \"hello\nquestion_tracker: true\nend: w\"\n---\n"),
            ("qt_legacy_ambig3", "---\ntitle: [&a \"hello ]\nquestion_tracker: true\nend: w\"]\n---\n"),
            ("qt_legacy_ambig4", "---\ntitle: A\n# example\u2028question_tracker: true\n---\n"),
            ("qt_legacy_ambig5", "---\n\x0b\nquestion_tracker: true\n---\n"),
            ("qt_legacy_ambig6", "---\nquestion_tracker: true\n---\x0b\nquestion_tracker: false\n---\n"),
        ]:
            q = qt_vault(name, claude_md="Questions: `_meta/open-questions.md`.\n",
                         pages={"_meta/open-questions.md": "# Q\n", "wiki/network/x.md": text})
            path, src, w = mp.resolve_question_tracker(q)
            check(path == str(q / "_meta/open-questions.md") and src == "legacy" and w == [],
                  f"{name}: {src} {w}")
        # the legacy file carrying the flag itself is not a redundancy
        q = qt_vault("qt_legacy_self", claude_md="Questions: `_meta/open-questions.md`.\n",
                     pages={"_meta/open-questions.md": FLAGGED})
        path, src, w = mp.resolve_question_tracker(q)
        check(src == "legacy" and w == [], f"qt_legacy_self: {src} {w}")
        # legacy wins over a flag; the flag earns a redundancy warning only
        q = qt_vault("qt_legacy_flag", claude_md="Questions: `_meta/open-questions.md`.\n",
                     pages={"_meta/open-questions.md": "# Q\n", "wiki/network/queue.md": FLAGGED})
        path, src, w = mp.resolve_question_tracker(q)
        check(path == str(q / "_meta/open-questions.md") and src == "legacy",
              f"qt_legacy_flag: flag displaced the legacy tracker: {path} ({src})")
        check(len(w) == 1 and "takes precedence" in w[0] and "wiki/network/queue.md" in w[0],
              f"qt_legacy_flag: {w}")
        # legacy + unreadable page -> legacy, no warning (unreadable only matters to the flag)
        q = qt_vault("qt_legacy_unread", claude_md="See for-reviewer.md.\n",
                     pages={"_meta/for-reviewer.md": "# Q\n"})
        (q / "wiki" / "network" / "locked.md").mkdir()
        path, src, w = mp.resolve_question_tracker(q)
        check(path == str(q / "_meta/for-reviewer.md") and src == "legacy" and w == [],
              f"qt_legacy_unread: {path} {src} {w}")
        # a legacy file that exists but is NOT named does not outrank a flag
        q = qt_vault("qt_unnamed_flag", claude_md="Nothing named.\n",
                     pages={"_meta/open-questions.md": "# Q\n", "wiki/network/queue.md": FLAGGED})
        path, src, w = mp.resolve_question_tracker(q)
        check(path == str(q / "wiki/network/queue.md") and src == "flag" and w == [],
              f"qt_unnamed_flag: {path} {src} {w}")
        # end to end: preflight reports the source; the legacy path reports "legacy"
        v = build_vault(base, "qt_e2e")
        (v / "wiki" / "concepts" / "queue.md").write_text(
            FLAGGED + "\nBack to [[start-here]].\n", encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["question_tracker"] == str(v / "wiki/concepts/queue.md")
              and rep["question_tracker_source"] == "flag",
              f"qt_e2e: {rep['question_tracker']} ({rep.get('question_tracker_source')})")
        check(not any("question tracker" in x for x in rep["warnings"]),
              f"qt_e2e: spurious warning {rep['warnings']}")
        rep = mp.preflight(base / "qt_found")
        check(rep["question_tracker_source"] == "legacy",
              f"qt_found: source {rep.get('question_tracker_source')}")

        # 12. CLI: --json --root emits one JSON object; exit 0 ready / 1 blocked
        v = build_vault(base, "cli", anchored=1)
        r = subprocess.run([sys.executable, str(SCRIPTS / "maintenance_preflight.py"),
                            "--json", "--root", str(v)],
                           capture_output=True, text=True, errors="replace",
                           cwd=str(base))
        try:
            out = json.loads(r.stdout)
        except json.JSONDecodeError:
            out = None
        check(out is not None, f"cli: stdout is not JSON: {r.stdout[:200]!r} {r.stderr[:200]!r}")
        if out:
            check(out["ready"] is True and r.returncode == 0,
                  f"cli: ready={out['ready']} rc={r.returncode} blockers={out['blockers']}")
            check(Path(out["root"]) == v.resolve(), f"cli: root={out['root']}")
            for key in ("ready", "blockers", "warnings", "root", "scanners", "park_to",
                        "question_tracker", "question_tracker_source", "edit_deny",
                        "triggers", "baseline",
                        "template_sync"):
                check(key in out, f"cli: missing top-level key {key}")
        r = subprocess.run([sys.executable, str(SCRIPTS / "maintenance_preflight.py"),
                            "--json", "--root", str(base / "noledger")],
                           capture_output=True, text=True, errors="replace")
        check(r.returncode == 1, f"cli blocked: rc={r.returncode}")

        # 13. baseline PRESENT but {"files": {}} (a vault bootstrapped with an
        # empty raw/) -> check_raw would re-initialize and WRITE. Must be
        # blocked with check_raw never invoked and the file byte-identical.
        # Bytes, not mtime alone: check_raw's timestamp is minute-granular, so
        # a rewrite inside the same minute could still differ only in bytes
        # (this hand-written payload has no _baseline_updated key at all, so
        # any rewrite by check_raw necessarily changes the bytes).
        def untouched_baseline_case(name: str, payload: str) -> None:
            v = build_vault(base, name, init_baseline=False)
            hp = v / "_meta" / "raw-hashes.json"
            hp.write_bytes(payload.encode("utf-8"))
            bytes_before, mtime_before = hp.read_bytes(), hp.stat().st_mtime_ns
            rep = mp.preflight(v)
            check(rep["ready"] is False, f"{name}: ready should be False")
            check(rep["scanners"]["check_raw.py"].startswith("not run"),
                  f"{name}: check_raw was invoked: {rep['scanners']['check_raw.py']}")
            check(any("check_raw.py --init" in b for b in rep["blockers"]),
                  f"{name}: blocker does not tell the operator how to clear it: {rep['blockers']}")
            check("raw_files" not in rep["baseline"], f"{name}: raw counts leaked {rep['baseline']}")
            check(hp.read_bytes() == bytes_before, f"{name}: baseline bytes CHANGED")
            check(hp.stat().st_mtime_ns == mtime_before, f"{name}: baseline mtime CHANGED")

        untouched_baseline_case("emptyfiles", '{"files": {}}')
        # 14. malformed JSON -> blocked, not invoked, file untouched
        untouched_baseline_case("badjson", '{"files": {')
        # variants of "no usable files mapping"
        untouched_baseline_case("emptyobj", '{}')
        untouched_baseline_case("nofileskey", '{"_baseline_updated": "2026-01-01 00:00 UTC"}')

        # 15. rounding boundary: lint prints {pct:.0f}, so 119/200 = 59.5%
        # prints as "60%" -- the trigger must decide on the counts, not the
        # printed percentage. 119/200 -> due (under 60% in truth).
        v = build_vault(base, "round_under", anchored=119, unanchored=81)
        rep = mp.preflight(v)
        ab = rep["triggers"]["anchor_backfill"]
        check(rep["scanners"]["lint.py"] == "ok", f"round_under: lint={rep['scanners']['lint.py']}")
        check(rep["baseline"].get("anchor_covered") == 119 and ab["eligible"] == 200,
              f"round_under: counts {rep['baseline'].get('anchor_covered')}/{ab['eligible']}")
        check(ab["coverage_pct"] == 60, f"round_under: expected lint to print 60%, got {ab}")
        check(ab["due"] is True, f"round_under: 119/200 must be due: {ab}")
        # 120/200 = exactly 60% -> not due
        v = build_vault(base, "round_exact", anchored=120, unanchored=80)
        ab = mp.preflight(v)["triggers"]["anchor_backfill"]
        check(ab["coverage_pct"] == 60 and ab["due"] is False,
              f"round_exact: 120/200 must not be due: {ab}")

        # 16. scanner timeout -> blocked, status names the timeout
        v = build_vault(base, "slow")
        (v / "scripts" / "check_stale.py").write_text(
            "import time\ntime.sleep(20)\n", encoding="utf-8")
        saved_timeout = mp.SCANNER_TIMEOUT
        mp.SCANNER_TIMEOUT = 1
        try:
            rep = mp.preflight(v)
        finally:
            mp.SCANNER_TIMEOUT = saved_timeout
        check(rep["ready"] is False, "slow: ready should be False")
        check(rep["scanners"]["check_stale.py"].startswith("failed: timed out"),
              f"slow: {rep['scanners']['check_stale.py']}")
        check(any("check_stale.py" in b and "did not complete" in b for b in rep["blockers"]),
              f"slow: blockers {rep['blockers']}")
        check("open_loops_open" not in rep["baseline"], "slow: partial counts leaked")

        # 17. sync_from_template.py unavailable -> warning, NOT a blocker
        v = build_vault(base, "nosync")
        (v / "scripts" / "sync_from_template.py").unlink()
        rep = mp.preflight(v)
        check(rep["ready"] is True and rep["blockers"] == [],
              f"nosync: sync absence must not block: {rep['blockers']}")
        check(rep["template_sync"]["status"] == "unavailable", f"nosync: {rep['template_sync']}")
        check(any("sync_from_template.py" in w and "drift unknown" in w for w in rep["warnings"]),
              f"nosync: no drift-unknown warning in {rep['warnings']}")

        # 18. raw/ change pending ingest -> warning, still ready, counts real
        v = build_vault(base, "rawpending")
        (v / "raw" / "new-source.txt").write_text("unread\n", encoding="utf-8")
        rep = mp.preflight(v)
        check(rep["ready"] is True, f"rawpending: blockers {rep['blockers']}")
        check(rep["baseline"].get("raw_added") == 1 and rep["baseline"].get("raw_files") == 2,
              f"rawpending: counts {rep['baseline']}")
        check(any("pending ingest" in w and "1 added" in w for w in rep["warnings"]),
              f"rawpending: no pending warning in {rep['warnings']}")

        # 19. gate 1 (regression R81): the edit-deny set is RESOLVED in code, not
        # prose. wiki/log.md by path rule; it must not block, and resolving it
        # must not write anything.
        v = build_vault(base, "deny_log")
        (v / "wiki" / "log.md").write_text(
            "---\ntitle: Log\ndescription: Append-only chronological record.\n---\n"
            "# Log\n\n## [2026-05-03] lint | replaced the `../../` link depth\n",
            encoding="utf-8")
        before = file_set(v)
        rep = mp.preflight(v)
        ed = rep["edit_deny"]
        check(ed["paths"] == ["wiki/log.md"], f"deny_log: paths {ed['paths']}")
        check(ed["by_rule"]["path"] == ["wiki/log.md"] and ed["by_rule"]["frontmatter"] == [],
              f"deny_log: by_rule {ed['by_rule']}")
        check(ed["unreadable"] == [] and "path rule" in ed["note"],
              f"deny_log: note/unreadable {ed}")
        check(rep["ready"] is True and rep["blockers"] == [],
              f"deny_log: gate 1 must not block: {rep['blockers']}")
        check(file_set(v) == before, "deny_log: resolving the deny set changed the vault")

        # 19b. frontmatter rule: `append_only: true` under wiki/ OR _meta/,
        # case/space-insensitive like _wikilib.fm_flag; `false`/absent is not
        # denied; and a frontmatter longer than the bounded head read still
        # resolves (a split fence must never read as "no flag").
        v = build_vault(base, "deny_fm")
        (v / "wiki" / "concepts" / "ledger.md").write_text(
            "---\ntitle: Ledger\ndescription: a hand ledger.\nappend_only: true\n---\n"
            "# Ledger\n\n- row\n", encoding="utf-8")
        (v / "wiki" / "concepts" / "notledger.md").write_text(
            "---\ntitle: Not a ledger\ndescription: ordinary page.\nappend_only: false\n---\n"
            "# Not a ledger\n", encoding="utf-8")
        (v / "_meta" / "decisions.md").write_text(
            "---\ntitle: Decisions\ndescription: standing decisions.\nAppend_Only:  True\n---\n"
            "# Decisions\n", encoding="utf-8")
        pad = "\n".join(f"alias_{i}: filler value {i}" for i in range(400))
        (v / "wiki" / "concepts" / "bigfm.md").write_text(
            "---\ntitle: Big\ndescription: frontmatter past the head slice.\n"
            + pad + "\nappend_only: true\n---\n# Big\n", encoding="utf-8")
        check(len(pad) > mp.FM_HEAD_CHARS, "deny_fm: filler shorter than the head read")
        rep = mp.preflight(v)
        ed = rep["edit_deny"]
        check(sorted(ed["by_rule"]["frontmatter"]) ==
              ["_meta/decisions.md", "wiki/concepts/bigfm.md", "wiki/concepts/ledger.md"],
              f"deny_fm: frontmatter rule resolved {ed['by_rule']['frontmatter']}")
        check(ed["by_rule"]["path"] == [], f"deny_fm: no log.md here: {ed['by_rule']['path']}")
        check("wiki/concepts/notledger.md" not in ed["paths"],
              f"deny_fm: `append_only: false` denied: {ed['paths']}")
        check(rep["ready"] is True, f"deny_fm: blockers {rep['blockers']}")

        # 19c. no log.md and no marker -> empty set, but NEVER a silent zero:
        # note says so in words (the R79 failure class in a new place).
        v = build_vault(base, "deny_none")
        rep = mp.preflight(v)
        ed = rep["edit_deny"]
        check(ed["paths"] == [] and ed["by_rule"] == {"path": [], "frontmatter": []},
              f"deny_none: {ed}")
        check(ed["note"] and "none resolved" in ed["note"],
              f"deny_none: silent zero -- note={ed['note']!r}")
        check(rep["ready"] is True and rep["blockers"] == [],
              f"deny_none: an empty deny set must not block: {rep['blockers']}")

        # 19d. the pass WRITES parked items into _meta/open-loops.md, so a
        # vault that marks its own parking destination append-only gets a
        # warning rather than a silent deadlock.
        v = build_vault(base, "deny_park")
        (v / "_meta" / "open-loops.md").write_text(
            "---\ntitle: Open Loops\ndescription: ledger.\nappend_only: true\n---\n"
            "# Open Loops\n\nBack to [[start-here]].\n", encoding="utf-8")
        rep = mp.preflight(v)
        check("_meta/open-loops.md" in rep["edit_deny"]["paths"],
              f"deny_park: {rep['edit_deny']['paths']}")
        check(any("parking destination" in w and "edit-deny" in w for w in rep["warnings"]),
              f"deny_park: no conflict warning in {rep['warnings']}")
        check(rep["ready"] is True and rep["park_to"], f"deny_park: {rep['blockers']}")

        # 19e. an unreadable page FAILS CLOSED. Its `append_only` state is
        # UNKNOWN, not false, so it lands in `unreadable` AND in `paths` --
        # a gate that warns about a file it then leaves out of its own deny
        # list fails OPEN, which is the failure class this build exists to
        # remove. A directory named like a page is the portable way to force
        # the OSError a locked or mid-Dropbox-sync file raises
        # (PermissionError on Windows, IsADirectoryError on POSIX).
        v = build_vault(base, "deny_unread")
        (v / "wiki" / "concepts" / "locked.md").mkdir()
        ed = mp.edit_deny(v)
        check(ed["unreadable"] == ["wiki/concepts/locked.md"],
              f"deny_unread: unreadable {ed['unreadable']}")
        check("wiki/concepts/locked.md" in ed["paths"],
              f"deny_unread: unreadable page dropped from paths -- gate fails OPEN: {ed}")
        check(ed["paths"] == ed["by_rule"]["path"] + ed["by_rule"]["frontmatter"]
              + ed["unreadable"], f"deny_unread: paths invariant broken: {ed}")
        check("unreadable" in ed["note"], f"deny_unread: note {ed['note']!r}")

        # and end to end: preflight reports it denied, warns, and still does
        # not block. fm_flag_true is monkeypatched rather than building a
        # second directory-as-page, so the scanners see an ordinary vault.
        v = build_vault(base, "deny_unread_park")
        orig_flag = mp.fm_flag_true
        mp.fm_flag_true = (lambda p, key, _o=orig_flag:
                           None if p.name == "open-loops.md" else _o(p, key))
        try:
            rep = mp.preflight(v)
        finally:
            mp.fm_flag_true = orig_flag
        check("_meta/open-loops.md" in rep["edit_deny"]["paths"],
              f"deny_unread_park: {rep['edit_deny']}")
        check(any("UNKNOWN, not false" in w and "edit_deny.paths" in w
                  for w in rep["warnings"]),
              f"deny_unread_park: no unreadable warning in {rep['warnings']}")
        check(any("parking destination" in w and "could not be read" in w
                  for w in rep["warnings"]),
              f"deny_unread_park: no destination warning in {rep['warnings']}")
        check(rep["ready"] is True and rep["blockers"] == [],
              f"deny_unread_park: gate 1 must not block: {rep['blockers']}")

        # ---------- regression R109: check_stale B3 INVALID / E MISSING SCAFFOLD ----------
        # A canned check_stale --summary report stands in for the scanner
        # (written as a fake scripts/check_stale.py that prints it verbatim),
        # so the counts and detail lines are exactly the ones under test.
        # RED control: before R109 parse_stale read A/B/B2/C/D only, so the
        # detail lines below never reached the report -- the assertions that
        # they are PRESENT in warnings are the ones the old code fails.
        # Third line: a root-level page whose path carries a space and an
        # apostrophe (a real vault's `wiki/O'Brien's Notes.md` shape);
        # a `\S+` path pattern silently dropped it (adversarial review of R109).
        B3_INVALID_LINES = [
            "  wiki/concepts/concept.md:9  INVALID: 'could-not-find'",
            "  wiki/entities/thing.md:14  INVALID: '(no reason given)'",
            "  wiki/O'Brien's Notes.md:3  INVALID: 'lost'",
        ]
        E_MISSING_LINES = ["  MISSING SCAFFOLD : _meta/llm-wiki.md"]

        def canned_stale(*, b3: bool = True, e: bool = True, b3_suffix: str = "",
                         b3_lines: list = B3_INVALID_LINES) -> str:
            parts = [
                "=== A. OPEN-LOOPS LEDGER === 0 open / 0 overdue  (_meta/open-loops.md)", "",
                "=== B. TIME-SENSITIVE MARKERS -- content pages === 1 hits in 1 files", "",
                "=== B2. MARKERS on tracker pages (open items EXPECTED here) === 0 hits", "",
            ]
            if b3:
                parts += [f"=== B3. TERMINAL *(unverifiable:)* LABELS === 3 valid / "
                          f"{len(b3_lines)} INVALID{b3_suffix}", *b3_lines,
                          "    (closed vocabulary: source-gone | paywalled | provenance-lost "
                          "-- see the marker-hygiene",
                          "     section of _meta/fleet-conventions.md; the story goes in the "
                          "log.md entry)", ""]
            parts += ["=== C. CENTRAL PAGES not updated in > 45 days === 0", "",
                      "=== D. RECURRING ITEMS ('Next due:' stamps) === 0 tracked / 0 DUE",
                      "  claim audit: NOT INSTALLED -- no stamp names it", ""]
            if e:
                parts += ["=== E. INSTANCE SCAFFOLD === 7 required / 1 missing",
                          *E_MISSING_LINES,
                          "    (instance-owned -- sync_from_template.py never ships these.)", ""]
            parts += ["=== F. TRANSCRIPT PROVENANCE === 1 transcript pages / 0 missing / "
                      "1 invalid / 0 legacy",
                      "  wiki/sources/talk.md  INVALID transcript_kind: 'bogus' is not a "
                      "transcript_kind  [tag video]", ""]
            return "\n".join(parts) + "\n"

        def fake_stale_vault(name: str, text: str) -> Path:
            v = build_vault(base, name)
            (v / "scripts" / "check_stale.py").write_text(
                "import sys\nsys.stdout.write(" + repr(text) + ")\n", encoding="utf-8")
            return v

        v = fake_stale_vault("stale_b3e", canned_stale())
        rep = mp.preflight(v)
        bl = rep["baseline"]
        check(rep["scanners"]["check_stale.py"] == "ok",
              f"b3e: check_stale state {rep['scanners']['check_stale.py']}")
        check(bl.get("terminal_labels_valid") == 3 and bl.get("terminal_labels_invalid") == 3,
              f"b3e: B3 counts {bl.get('terminal_labels_valid')}/{bl.get('terminal_labels_invalid')}")
        check(bl.get("scaffold_required") == 7 and bl.get("scaffold_missing") == 1,
              f"b3e: E counts {bl.get('scaffold_required')}/{bl.get('scaffold_missing')}")
        # the detail lines are PRESENT, verbatim enough to act on (file:line + slug / path)
        inv_w = [w for w in rep["warnings"] if "INVALID terminal label" in w]
        gap_w = [w for w in rep["warnings"] if "MISSING SCAFFOLD" in w]
        check(len(inv_w) == 3, f"b3e: expected 3 INVALID-label warnings, got {inv_w}")
        check(any("wiki/concepts/concept.md:9" in w and "'could-not-find'" in w for w in inv_w),
              f"b3e: first INVALID line not carried: {inv_w}")
        check(any("wiki/entities/thing.md:14" in w and "'(no reason given)'" in w for w in inv_w),
              f"b3e: second INVALID line not carried: {inv_w}")
        check(any("wiki/O'Brien's Notes.md:3" in w and "'lost'" in w for w in inv_w),
              f"b3e: space/apostrophe path INVALID line not carried: {inv_w}")
        check(len(gap_w) == 1 and "_meta/llm-wiki.md" in gap_w[0],
              f"b3e: MISSING SCAFFOLD line not carried: {gap_w}")
        check(not any("detail line(s) parsed" in w for w in rep["warnings"]),
              f"b3e: spurious count/detail mismatch warning in {rep['warnings']}")
        # the section-F INVALID transcript_kind line is NOT mistaken for a B3 label
        check(not any("talk.md" in w for w in inv_w),
              f"b3e: section F line parsed as a B3 label: {inv_w}")
        # warnings, never blockers: READY unchanged
        check(rep["ready"] is True and rep["blockers"] == [],
              f"b3e: INVALID/MISSING must warn, not block: ready={rep['ready']} {rep['blockers']}")
        # the human report carries them too (the channel a launcher reads)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mp.print_human(rep)
        human = buf.getvalue()
        check("wiki/concepts/concept.md:9" in human and "_meta/llm-wiki.md" in human
              and "READY : yes" in human, "b3e: human report lacks the detail lines or READY")

        # the R99 suffix variant: ` / K UNLOGGED` after INVALID must still parse
        counts, err = mp.parse_stale(canned_stale(b3_suffix=" / 1 UNLOGGED"))
        check(err is None and counts.get("terminal_labels_invalid") == 3
              and counts.get("terminal_labels_valid") == 3,
              f"b3 suffix: err={err} counts={counts}")
        v = fake_stale_vault("stale_b3_suffix", canned_stale(b3_suffix=" / 1 UNLOGGED"))
        rep = mp.preflight(v)
        check(rep["ready"] is True
              and len([w for w in rep["warnings"] if "INVALID terminal label" in w]) == 3,
              f"b3 suffix e2e: ready={rep['ready']} warnings={rep['warnings']}")

        # an older check_stale without B3 / E: parses exactly as before, no key,
        # no warning, no blocker
        v = fake_stale_vault("stale_old", canned_stale(b3=False, e=False))
        rep = mp.preflight(v)
        check(rep["scanners"]["check_stale.py"] == "ok" and rep["ready"] is True,
              f"old: {rep['scanners']['check_stale.py']} ready={rep['ready']} {rep['blockers']}")
        for k in ("terminal_labels_valid", "terminal_labels_invalid",
                  "scaffold_required", "scaffold_missing"):
            check(k not in rep["baseline"], f"old: fabricated {k}={rep['baseline'].get(k)}")
        check(not any("INVALID terminal label" in w or "MISSING SCAFFOLD" in w
                      or "detail line(s) parsed" in w for w in rep["warnings"]),
              f"old: B3/E warning without the sections: {rep['warnings']}")

        # a header count that the detail lines do not account for is said out
        # loud, never silently truncated to the lines that did parse
        # (produced by a header edit only -- every detail line stays parseable)
        v = fake_stale_vault("stale_mismatch", canned_stale(b3_lines=B3_INVALID_LINES[:1])
                             .replace("=== 3 valid / 1 INVALID", "=== 3 valid / 2 INVALID"))
        rep = mp.preflight(v)
        check(rep["ready"] is True and any("counts 2 INVALID" in w and "1 detail line(s) parsed" in w
                                           for w in rep["warnings"]),
              f"mismatch: ready={rep['ready']} warnings={rep['warnings']}")

    if fails:
        print("FAIL")
        for f in fails:
            print(f"  {f}")
        return 1
    print("PASS: healthy vault ready; unparseable/non-zero/missing/timed-out scanners "
          "block with no fabricated zeros; ledger required and never created; tracked=0 "
          "warns; claim_audit.due only from the claim-audit stamp (other due items -> "
          "recurring_due_other + warning; wrong-stamp -> cadence warning; log.md "
          "excluded; a recorded exemption is a decision, not a warning); anchor boundary correct on counts (119/200 due, 120/200 not); "
          "empty/malformed raw baseline blocks with check_raw never run and the file "
          "untouched; sync absence and raw pending are warnings; legacy question tracker "
          "needs presence + mention and outranks a `question_tracker: true` flag, which "
          "is chosen only when provably unique and strictly parsed (two flags, an "
          "unreadable page or an ambiguous top-level value -> unresolved; indented "
          "text is not the key); edit-deny set resolves log.md by path and "
          "`append_only: true` by frontmatter, denies an unreadable page (it is "
          "in paths, not merely warned about), never blocks, never writes, and "
          "says 'none resolved' in words rather than showing an empty section; "
          "a missing or corrupt scripts/_wikilib.py makes "
          "lint exit 1 and blocks with a remedy-naming message, not a traceback; "
          "check_stale B3 INVALID labels and E MISSING SCAFFOLD lines reach the report "
          "as warnings verbatim (READY unchanged, ` / K UNLOGGED` suffix parses, absent "
          "sections = no key and no warning, count/detail mismatch said out loud)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
